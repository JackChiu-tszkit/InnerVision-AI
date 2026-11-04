"""意图识别、MCP 授权和只读 LangChain 工具循环。"""

from __future__ import annotations

from pathlib import Path

import pytest
from langchain_core.messages import AIMessage, ToolMessage

from innervision.agent.prompts import build_system_prompt, load_skill
from innervision.agent.router import classify
from innervision.config import load_settings
from innervision.mcp.server import LLM_VISIBLE_TOOLS
from tests.helpers import commit

ROOT = Path(__file__).resolve().parents[1]
SENTINEL = "哨兵阳光不该出现在日志"


def test_router_recognizes_four_intents() -> None:
    assert classify("帮我记录感恩").intent == "gratitude"
    assert classify("帮我记录感恩").mode == "write"
    assert classify("看看本周感恩回顾").mode == "read"
    assert classify("创建愿景：每天散步").intent == "vision"
    assert classify("我想做释放练习").intent == "sedona"
    assert classify("我想做释放练习").mode == "practice"
    assert classify("看看释放练习记录").mode == "read"
    assert classify("添加P1任务：喝水").intent == "action"
    assert classify("我今天很难过").intent == "unknown"
    assert classify("胸口还是紧", sedona_open=True).intent == "sedona"


def test_orchestrator_proposes_without_confirming(container) -> None:
    reply = container.orchestrator.handle("帮我记录感恩：\n1. 甲甲甲\n2. 乙乙乙\n3. 丙丙丙")
    assert reply.pending_token
    assert reply.pending_action == "gratitude.create"
    assert container.auth.is_confirmed(reply.pending_token) is False
    assert container.gratitude.list_recent() == []
    commit(container, reply.pending_token)
    assert container.gratitude.list_recent()[0]["item_1"] == "甲甲甲"


def test_orchestrator_asks_once_when_priority_missing(container) -> None:
    reply = container.orchestrator.handle("添加任务：去公园走走")
    assert reply.pending_token is None
    assert reply.message.count("？") == 1
    assert "P1" in reply.message and "P2" in reply.message


def test_orchestrator_completes_action_after_confirmation(container) -> None:
    created = container.orchestrator.handle("添加P1任务：给妈妈打电话")
    commit(container, created.pending_token)
    finishing = container.orchestrator.handle("完成行动：给妈妈打电话")
    assert finishing.pending_action == "action.complete"
    assert container.auth.is_confirmed(finishing.pending_token) is False
    commit(container, finishing.pending_token)
    tasks = container.actions.list_tasks(status="done")
    assert tasks[0]["title"] == "给妈妈打电话"


def test_weekly_read_uses_mcp_and_skips_logs(container) -> None:
    commit(
        container,
        container.gratitude.propose_create(
            {
                "entry_date": "2026-11-04",
                "item_1": SENTINEL,
                "item_2": "热茶",
                "item_3": "散步",
                "reflection": "",
            }
        ),
    )
    reply = container.orchestrator.handle("看看本周感恩回顾")
    assert SENTINEL in reply.message
    log_text = (container.settings.log_dir / "app.log").read_text(encoding="utf-8")
    assert SENTINEL not in log_text


def test_crisis_in_chat_stops_practice_without_saving_text(container) -> None:
    started = commit(container, container.sedona.propose_start())
    reply = container.orchestrator.handle("我不想活了")
    assert reply.safety_stop is True
    assert "12356" in reply.message
    detail = container.sedona.get_detail(started["id"])
    assert detail["state"] == "safety_stop"
    assert "不想活了" not in " ".join(turn["content"] for turn in detail["turns"])


def test_mcp_commit_requires_user_confirmation(container) -> None:
    proposed = container.mcp.call(
        "propose_gratitude_entry",
        {
            "entry_date": "2026-11-04",
            "item_1": "甲",
            "item_2": "乙",
            "item_3": "丙",
            "reflection": "",
        },
    )
    assert proposed["ok"] is True
    denied = container.mcp.call("commit_write", {"token": proposed["token"]})
    assert denied["ok"] is False
    assert container.gratitude.list_recent() == []
    container.auth.confirm(proposed["token"])
    saved = container.mcp.call("commit_write", {"token": proposed["token"]})
    assert saved["ok"] is True
    assert container.gratitude.list_recent()[0]["item_1"] == "甲"


def test_mcp_sedona_summary_hides_feeling(container) -> None:
    started = commit(container, container.sedona.propose_start())
    commit(container, container.sedona.propose_step(started["id"], SENTINEL))
    summary = container.mcp.call("sedona_session_summary", {"limit": 5})
    assert summary["ok"] is True
    assert SENTINEL not in str(summary)


def test_tool_allowlist_has_no_shell_or_delete_all(container) -> None:
    names = set(container.mcp.list_tool_names())
    assert "commit_write" in names
    assert "list_gratitude_entries" in names
    assert "propose_delete_all" not in names
    for banned in ("shell", "exec", "run_command", "read_file", "write_file", "delete_all"):
        assert banned not in names
    visible = {tool.name for tool in container.read_tools}
    assert visible == LLM_VISIBLE_TOOLS
    assert "commit_write" not in visible


def test_readonly_agent_calls_mcp_tool(container) -> None:
    commit(
        container,
        container.gratitude.propose_create(
            {
                "entry_date": "2026-11-04",
                "item_1": SENTINEL,
                "item_2": "热茶",
                "item_3": "散步",
                "reflection": "",
            }
        ),
    )
    fake = _FakeLLM()
    container.read_agent.llm = fake
    answer = container.read_agent.run("系统", "看看感恩")
    assert answer == "本周有记录"
    tool_messages = [message for message in fake.seen[-1] if isinstance(message, ToolMessage)]
    assert tool_messages
    assert SENTINEL in tool_messages[0].content
    log_text = (container.settings.log_dir / "app.log").read_text(encoding="utf-8")
    assert SENTINEL not in log_text


def test_polished_reply_is_rejected_when_it_promises(container) -> None:
    class Promising:
        def run(self, system: str, user: str) -> str:
            return "我保证你一定会好起来，已经写入。"

    container.orchestrator.read_agent = Promising()
    container.orchestrator.llm_available = lambda: True
    reply = container.orchestrator.handle("看看本周感恩回顾")
    assert "保证" not in reply.message
    assert "这一周" in reply.message


def test_skills_and_prompt_are_local_text() -> None:
    settings = load_settings()
    for intent in ("gratitude", "vision", "sedona", "action"):
        skill = load_skill(settings.skills_dir, intent)
        assert "不要" in skill
    prompt = build_system_prompt("技能正文")
    assert "不要承诺" in prompt
    assert "只读工具" in prompt


def test_source_has_no_shell_execution() -> None:
    banned = ("os.system", "subprocess", "shell=True", "os.popen", "eval(", "exec(")
    source = "\n".join(path.read_text(encoding="utf-8") for path in (ROOT / "src").rglob("*.py"))
    for token in banned:
        assert token not in source


def test_network_defaults_are_loopback(monkeypatch) -> None:
    monkeypatch.delenv("INNERVISION_OLLAMA_URL", raising=False)
    settings = load_settings()
    assert settings.mcp_host == "127.0.0.1"
    assert settings.streamlit_host == "127.0.0.1"
    monkeypatch.setenv("INNERVISION_OLLAMA_URL", "http://example.com:11434")
    with pytest.raises(ValueError):
        load_settings()
    config = (ROOT / ".streamlit" / "config.toml").read_text(encoding="utf-8")
    assert 'address = "127.0.0.1"' in config


class _FakeLLM:
    def __init__(self) -> None:
        self.calls = 0
        self.seen: list = []

    def bind_tools(self, tools):
        self.tools = tools
        return self

    def invoke(self, messages):
        self.calls += 1
        self.seen.append(messages)
        if self.calls == 1:
            return AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "list_gratitude_entries",
                        "args": {"limit": 5},
                        "id": "call-1",
                        "type": "tool_call",
                    }
                ],
            )
        return AIMessage(content="本周有记录")
