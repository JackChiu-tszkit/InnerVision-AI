"""对话协调器：识别意图，通过 MCP 读取，写入只生成待确认请求。"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from innervision.agent.extract import (
    extract_action,
    extract_gratitude_items,
    extract_vision,
    find_task_to_complete,
)
from innervision.agent.langchain_agent import ReadOnlyAgent
from innervision.agent.prompts import build_system_prompt, load_skill
from innervision.agent.router import classify
from innervision.logging_setup import safe_log
from innervision.mcp.client import SyncMCPClient
from innervision.safety import is_crisis_text, support_message
from innervision.services.sedona import CLOSED, SedonaService
from innervision.timeutil import SystemClock, monday_of

_BANNED_REPLY = ("诊断", "抑郁症", "焦虑症", "保证", "一定会", "必须放下", "已保存", "已经写入")


@dataclass
class AgentReply:
    """返回给界面的一轮回复。有待确认令牌时，界面必须再请用户点确认。"""

    intent: str
    mode: str
    message: str
    pending_token: str | None = None
    pending_action: str | None = None
    preview: dict | None = None
    safety_stop: bool = False


class Orchestrator:
    """本地助手。它不能自己确认写入。"""

    def __init__(
        self,
        mcp: SyncMCPClient,
        sedona: SedonaService,
        read_agent: ReadOnlyAgent,
        llm_available: Callable[[], bool],
        skills_dir: Path,
        clock: SystemClock | None = None,
    ) -> None:
        self.mcp = mcp
        self.sedona = sedona
        self.read_agent = read_agent
        self.llm_available = llm_available
        self.skills_dir = skills_dir
        self.clock = clock or SystemClock()

    def handle(self, message: str) -> AgentReply:
        """处理一句用户输入。安全风险会先于普通意图被处理。"""
        text = message.strip()
        if not text:
            return AgentReply("unknown", "chat", "可以写下一句你想记录或整理的话。")
        if is_crisis_text(text):
            self._stop_open_practice()
            return AgentReply("safety", "safety", support_message(), safety_stop=True)
        open_session = self._open_session()
        decision = classify(text, sedona_open=open_session is not None)
        safe_log("intent", intent=decision.intent, ok=1)
        if decision.intent == "sedona" and decision.mode == "practice":
            return self._practice(text, open_session)
        if decision.mode == "read":
            return self._read(decision.intent, text)
        if decision.mode == "write":
            return self._write(decision.intent, text)
        return self._chat(decision.intent)

    def _practice(self, text: str, open_session: dict | None) -> AgentReply:
        if open_session is None:
            return self._propose(
                "propose_sedona_start",
                {},
                "sedona",
                "确认后会开始一次释放练习。每次只有一个问题，你可以随时暂停、跳过或结束。",
            )
        return self._propose(
            "propose_sedona_step",
            {"session_id": int(open_session["id"]), "user_text": text},
            "sedona",
            "确认后才会保存这一步并继续。你可以改口，也可以取消。",
        )

    def _write(self, intent: str, text: str) -> AgentReply:
        today = self.clock.today().isoformat()
        if intent == "gratitude":
            items = extract_gratitude_items(text)
            if items is None:
                return AgentReply(intent, "write", "可以把三件感恩分成三行写下来。确认之前我不会保存。")
            return self._propose(
                "propose_gratitude_entry",
                {
                    "entry_date": today,
                    "item_1": items[0],
                    "item_2": items[1],
                    "item_3": items[2],
                    "reflection": "",
                },
                intent,
                "我整理了三件感恩。确认后才会写进这台电脑。",
            )
        if intent == "vision":
            draft = extract_vision(text)
            if draft is None:
                return AgentReply(intent, "write", "想创建愿景的话，可以用“愿景：标题”这样写。确认之前我不会保存。")
            return self._propose(
                "propose_vision_card",
                {
                    "title": draft["title"],
                    "category": draft["category"],
                    "description": draft["description"],
                    "target_date": draft["target_date"] or "",
                    "milestones": "\n".join(draft["milestones"]),
                },
                intent,
                "我整理了一张愿景卡片。确认后才会保存。",
            )
        if intent == "action":
            tasks = self._tasks()
            task_id = find_task_to_complete(text, tasks)
            if task_id is not None:
                return self._propose(
                    "propose_complete_action",
                    {"task_id": task_id},
                    intent,
                    "确认后才会把这项行动标为完成。",
                )
            draft = extract_action(text)
            if draft is None:
                if re.search(r"任务|行动", text):
                    return AgentReply(
                        intent,
                        "write",
                        "这件事情是紧急且重要（P1），还是为未来积累（P2）？",
                    )
                return AgentReply(intent, "write", "可以用“添加P1任务：标题”或“添加P2任务：标题”来建立行动。")
            return self._propose(
                "propose_action_task",
                {
                    "title": draft["title"],
                    "priority": draft["priority"],
                    "detail": draft["detail"],
                    "vision_id": draft["vision_id"] or 0,
                },
                intent,
                "我整理了一项行动。确认后才会保存。",
            )
        return self._chat(intent)

    def _read(self, intent: str, text: str) -> AgentReply:
        deterministic = self._deterministic_read(intent)
        if self.llm_available():
            try:
                system = build_system_prompt(load_skill(self.skills_dir, intent))
                polished = self.read_agent.run(system, text)
            except Exception:
                safe_log("read_agent_fallback", intent=intent, reason="llm_error", ok=0)
                polished = ""
            if _acceptable(polished):
                return AgentReply(intent, "read", polished)
        return AgentReply(intent, "read", deterministic)

    def _deterministic_read(self, intent: str) -> str:
        start = monday_of(self.clock.today()).isoformat()
        if intent == "gratitude":
            data = self._ok(self.mcp.call("gratitude_weekly_review", {"week_start": start}))
            lines = [str(data.get("narrative") or "")]
            for item in data.get("items") or []:
                lines.append(f"- {item}")
            return "\n".join(line for line in lines if line)
        if intent == "vision":
            data = self._ok(self.mcp.call("list_vision_cards", {}))
            cards = data.get("cards") or []
            if not cards:
                return "还没有愿景卡片。"
            lines = ["目前的愿景卡片："]
            for card in cards:
                when = card.get("target_date") or "未设日期"
                lines.append(f"- {card['title']}（{card['category']}，{when}）")
            return "\n".join(lines)
        if intent == "action":
            data = self._ok(self.mcp.call("action_weekly_review", {"week_start": start}))
            lines = [str(data.get("narrative") or "")]
            for task in data.get("open_tasks") or []:
                lines.append(f"- {task['priority']} {task['title']}")
            return "\n".join(line for line in lines if line)
        if intent == "sedona":
            data = self._ok(self.mcp.call("sedona_session_summary", {"limit": 1}))
            sessions = data.get("sessions") or []
            if not sessions:
                return "还没有释放练习。"
            latest = sessions[0]
            if latest.get("safety_stopped"):
                return "最近一次练习已经停止。如果需要支持信息，可以打开释放练习页面。"
            return "有释放练习记录。具体引导请在释放练习页面继续，摘要里不会展开感受正文。"
        return self._chat(intent).message

    def _chat(self, intent: str) -> AgentReply:
        messages = {
            "gratitude": "感恩日记可以记下三件具体的事。你想现在记录，还是先看本周回顾？",
            "vision": "愿景卡片可以写下标题、分类和目标日期。你想创建一张，还是先看看已有的卡片？",
            "action": "行动可以分成 P1（紧急且重要）和 P2（为未来积累）。你想添加任务，还是查看本周？",
            "sedona": "释放练习每次只问一个问题。你可以随时暂停、跳过或结束。想现在开始吗？",
        }
        return AgentReply(
            intent,
            "chat",
            messages.get(intent, "我可以陪你整理感恩日记、愿景卡片、释放练习或行动任务。你想先做哪一件？"),
        )

    def _propose(self, tool: str, arguments: dict, intent: str, message: str) -> AgentReply:
        data = self._ok(self.mcp.call(tool, arguments))
        if not data.get("ok"):
            return AgentReply(intent, "write", str(data.get("message") or "这次还不能保存。"))
        return AgentReply(
            intent,
            "write",
            message,
            pending_token=str(data["token"]),
            pending_action=str(data.get("action") or ""),
            preview=dict(data.get("preview") or {}),
        )

    def _tasks(self) -> list[dict]:
        data = self._ok(self.mcp.call("list_action_tasks", {}))
        return list(data.get("tasks") or [])

    def _open_session(self) -> dict | None:
        data = self._ok(self.mcp.call("sedona_session_summary", {"limit": 5}))
        for session in data.get("sessions") or []:
            if session.get("state") not in CLOSED:
                return session
        return None

    def _stop_open_practice(self) -> None:
        current = self.sedona.get_open()
        if current is not None:
            self.sedona.stop_for_safety(int(current["id"]))

    def _ok(self, payload: object) -> dict:
        if isinstance(payload, dict):
            return payload
        return {"ok": False, "message": "读取失败"}


def _acceptable(text: str) -> bool:
    cleaned = text.strip()
    if not cleaned:
        return False
    return not any(word in cleaned for word in _BANNED_REPLY)


def week_start_for(day: date) -> str:
    """给调用方复用的周一日期。"""
    return monday_of(day).isoformat()
