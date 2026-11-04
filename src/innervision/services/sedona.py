"""释放练习的状态流程。模型只能改写问句，不能决定下一步。"""

from __future__ import annotations

import re
from typing import Protocol

from innervision.auth import AuthorizationService
from innervision.db import Database
from innervision.logging_setup import safe_log
from innervision.models import SedonaResume, SedonaStart, SedonaStep
from innervision.safety import is_crisis_text, is_safe_single_question, support_message
from innervision.services.common import write_audit
from innervision.timeutil import now_iso

INTAKE = "intake"
LOCATE = "locate"
ALLOW = "allow"
COULD = "could"
WOULD = "would"
WHEN = "when"
REFLECT = "reflect"
PAUSED = "paused"
COMPLETED = "completed"
SAFETY_STOP = "safety_stop"

FLOW = [INTAKE, LOCATE, ALLOW, COULD, WOULD, WHEN, REFLECT]
CLOSED = {COMPLETED, SAFETY_STOP}
MAX_TURNS = 40

QUESTIONS = {
    INTAKE: "此刻，你注意到自己里面有什么样的感受？",
    LOCATE: "这个感受在身体的哪里，强度大约是多少（0 到 10）？",
    ALLOW: "你是否愿意先允许这个感受留在这里，即使你并不喜欢它？",
    COULD: "你能不能放下它？不能也可以，只是如实回答。",
    WOULD: "你愿不愿意放下它？不愿意也完全可以。",
    WHEN: "你想在什么时候放下，现在、稍后，还是还不确定？",
    REFLECT: "现在你注意到什么？",
}

STATE_LABELS = {
    INTAKE: "觉察感受",
    LOCATE: "身体与强度",
    ALLOW: "允许它在这里",
    COULD: "探索能否放下",
    WOULD: "探索是否愿意",
    WHEN: "选择时间",
    REFLECT: "练习后反思",
    PAUSED: "已暂停",
    COMPLETED: "已结束",
    SAFETY_STOP: "已停止",
}

PAUSE_MESSAGE = "已经暂停。想继续时可以点击继续，现在不需要做任何事。"
END_MESSAGE = "练习已经结束。你写下的内容只留在这台电脑上。"
CLOSE_MESSAGE = "练习先到这里。谢谢你愿意停下来看看自己。记录只留在这台电脑上，你不需要有任何改变。"
LENGTH_MESSAGE = "这次练习的回合已经比较长，我们先在这里停下来。你的记录只留在本机。"
PAUSED_REPLY = "要继续、跳过，还是结束？"

_INTENSITY_RE = re.compile(r"(?<!\d)(10|[0-9])(?!\d)")
_NO_MARKERS = ("不愿意", "不想", "不要", "不可以", "不能", "不好", "暂不", "没法", "no")
_YES_MARKERS = ("愿意", "可以", "想放下", "yes", "好的", "好")
_UPDATABLE = {
    "state",
    "previous_state",
    "feeling_label",
    "body_note",
    "intensity_before",
    "intensity_after",
    "allow_answer",
    "could_answer",
    "would_answer",
    "when_answer",
    "reflection",
    "safety_stopped",
    "turn_count",
}


class QuestionPhraser(Protocol):
    """把固定问句改写得更自然。实现不能新增第二个问题。"""

    def phrase(self, template: str, step: str) -> str:
        """返回一个问句。"""


class TemplatePhraser:
    """不调用模型时，直接使用固定问句。"""

    def phrase(self, template: str, step: str) -> str:
        return template


def parse_command(text: str) -> str | None:
    """只把整句命令识别为暂停、跳过或结束，避免误伤普通回答。"""
    cleaned = text.strip().lower().strip("。.!！ ")
    commands = {
        "暂停": "pause",
        "pause": "pause",
        "跳过": "skip",
        "skip": "skip",
        "结束": "end",
        "结束练习": "end",
        "stop": "end",
        "end": "end",
    }
    return commands.get(cleaned)


def parse_intensity(text: str) -> int | None:
    """从回答里提取 0 到 10 的强度。没有数字时保持空白。"""
    match = _INTENSITY_RE.search(text or "")
    if match is None:
        return None
    return int(match.group(1))


def willingness(text: str) -> str:
    """判断用户对“是否愿意放下”的回答。不确定或不愿意都不会被推进到追问时间。"""
    folded = text.strip().lower()
    if any(marker in folded for marker in _NO_MARKERS):
        return "no"
    if any(marker in folded for marker in _YES_MARKERS):
        return "yes"
    return "unsure"


def next_linear(state: str) -> str:
    """按固定顺序进入下一步。跳过时使用这条路径。"""
    index = FLOW.index(state)
    if index + 1 >= len(FLOW):
        return COMPLETED
    return FLOW[index + 1]


def advance_answer(state: str, answer: str) -> str:
    """根据回答前进。不愿意或不确定放下时，跳过“什么时候”这一问。"""
    if state == WOULD and willingness(answer) != "yes":
        return REFLECT
    return next_linear(state)


class SedonaService:
    """用确定性状态机带领练习。每一步最多产生一个核心问题。"""

    def __init__(self, db: Database, auth: AuthorizationService, phraser: QuestionPhraser) -> None:
        self.db = db
        self.auth = auth
        self.phraser = phraser

    def propose_start(self) -> str:
        """提出开始一次练习。已有未结束练习时不会再开新的。"""
        if self.get_open() is not None:
            raise ValueError("已经有一次练习还没结束")
        return self.auth.propose("sedona.start", SedonaStart().model_dump())

    def propose_step(self, session_id: int, user_text: str) -> str:
        """提出保存一步回答。真正写入要等用户确认。"""
        data = SedonaStep(session_id=session_id, user_text=user_text)
        return self.auth.propose("sedona.step", data.model_dump())

    def propose_resume(self, session_id: int) -> str:
        """提出从暂停处继续。"""
        data = SedonaResume(session_id=session_id)
        return self.auth.propose("sedona.resume", data.model_dump())

    def start_authorized(self, token: str) -> dict:
        """核销授权后开始练习，并只提出第一个问题。"""
        if self.get_open() is not None:
            raise ValueError("已经有一次练习还没结束")
        message = self._ask(INTAKE)
        with self.db.session() as conn:
            payload = self.auth.consume(conn, token, "sedona.start")
            SedonaStart.model_validate(payload)
            moment = now_iso()
            cursor = conn.execute(
                """
                INSERT INTO sedona_sessions(
                    state, feeling_label, body_note, allow_answer, could_answer,
                    would_answer, when_answer, reflection, safety_stopped, turn_count,
                    created_at, updated_at
                ) VALUES (?, '', '', '', '', '', '', '', 0, 1, ?, ?)
                """,
                (INTAKE, moment, moment),
            )
            session_id = int(cursor.lastrowid)
            self._add_turn(conn, session_id, INTAKE, "assistant", message, moment)
            write_audit(conn, "sedona.start", "sedona", session_id, 1)
            return self._view(conn, session_id, message)

    def step_authorized(self, token: str) -> dict:
        """核销授权后处理一步。安全风险会立即停止，且不会把原文交给改写模型。"""
        with self.db.session() as conn:
            payload = self.auth.consume(conn, token, "sedona.step")
            data = SedonaStep.model_validate(payload)
            row = conn.execute(
                "SELECT * FROM sedona_sessions WHERE id = ?",
                (data.session_id,),
            ).fetchone()
            if row is None:
                raise ValueError("没有找到这次练习")
            session = dict(row)
            if session["state"] in CLOSED:
                raise ValueError("这次练习已经结束")
            if is_crisis_text(data.user_text):
                return self._stop_for_safety(conn, session)
            message, new_state, updates = self._transition(session, data.user_text)
            updates["state"] = new_state
            updates["turn_count"] = int(session["turn_count"]) + 2
            self._update(conn, data.session_id, updates)
            moment = now_iso()
            self._add_turn(conn, data.session_id, session["state"], "user", data.user_text, moment)
            self._add_turn(conn, data.session_id, new_state, "assistant", message, moment)
            write_audit(conn, "sedona.step", "sedona", data.session_id, 1)
            return self._view(conn, data.session_id, message)

    def resume_authorized(self, token: str) -> dict:
        """从暂停前的问题继续，并重新提出那一个问题。"""
        with self.db.session() as conn:
            payload = self.auth.consume(conn, token, "sedona.resume")
            data = SedonaResume.model_validate(payload)
            row = conn.execute(
                "SELECT * FROM sedona_sessions WHERE id = ?",
                (data.session_id,),
            ).fetchone()
            if row is None:
                raise ValueError("没有找到这次练习")
            session = dict(row)
            if session["state"] != PAUSED:
                raise ValueError("这次练习不在暂停中")
            restored = session.get("previous_state") or INTAKE
            if restored not in QUESTIONS:
                restored = INTAKE
            message = self._ask(restored)
            self._update(
                conn,
                data.session_id,
                {
                    "state": restored,
                    "previous_state": None,
                    "turn_count": int(session["turn_count"]) + 1,
                },
            )
            self._add_turn(conn, data.session_id, restored, "assistant", message, now_iso())
            write_audit(conn, "sedona.resume", "sedona", data.session_id, 1)
            return self._view(conn, data.session_id, message)

    def stop_for_safety(self, session_id: int) -> None:
        """立即停止开放中的练习。不接收、不保存用户原文。"""
        with self.db.session() as conn:
            row = conn.execute(
                "SELECT * FROM sedona_sessions WHERE id = ?",
                (session_id,),
            ).fetchone()
            if row is None or row["state"] in CLOSED:
                return
            self._stop_for_safety(conn, dict(row))

    def get_open(self) -> dict | None:
        """读取尚未结束的练习。"""
        with self.db.session() as conn:
            row = conn.execute(
                """
                SELECT * FROM sedona_sessions
                WHERE state NOT IN ('completed', 'safety_stop')
                ORDER BY id DESC
                LIMIT 1
                """
            ).fetchone()
        return dict(row) if row else None

    def list_summaries(self, limit: int = 10) -> list[dict]:
        """给模型和列表使用的摘要，不包含感受正文。"""
        safe_limit = min(max(int(limit), 1), 50)
        with self.db.session() as conn:
            rows = conn.execute(
                """
                SELECT id, state, safety_stopped, intensity_before, intensity_after,
                       created_at, updated_at
                FROM sedona_sessions
                ORDER BY id DESC
                LIMIT ?
                """,
                (safe_limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_detail(self, session_id: int) -> dict | None:
        """读取用户自己查看的练习详情。"""
        with self.db.session() as conn:
            row = conn.execute(
                "SELECT * FROM sedona_sessions WHERE id = ?",
                (session_id,),
            ).fetchone()
            if row is None:
                return None
            view = self._view(conn, session_id, "")
        assistants = [item for item in view["turns"] if item["role"] == "assistant"]
        if assistants:
            view["assistant_message"] = assistants[-1]["content"]
        return view

    def _transition(self, session: dict, text: str) -> tuple[str, str, dict]:
        state = session["state"]
        if int(session["turn_count"]) >= MAX_TURNS and parse_command(text) != "end":
            return LENGTH_MESSAGE, COMPLETED, {"previous_state": None}
        command = parse_command(text)
        if command == "pause":
            previous = session.get("previous_state") if state == PAUSED else state
            return PAUSE_MESSAGE, PAUSED, {"previous_state": previous}
        if command == "end":
            return END_MESSAGE, COMPLETED, {"previous_state": None}
        if command == "skip":
            base = session.get("previous_state") if state == PAUSED else state
            if base not in FLOW:
                base = INTAKE
            new_state = next_linear(base)
            message = CLOSE_MESSAGE if new_state == COMPLETED else self._ask(new_state)
            return message, new_state, {"previous_state": None}
        if state == PAUSED:
            return PAUSED_REPLY, PAUSED, {}
        updates = self._answer_updates(state, text)
        new_state = advance_answer(state, text)
        if new_state == COMPLETED:
            message = CLOSE_MESSAGE
        else:
            message = self._ask(new_state)
        updates["previous_state"] = None
        return message, new_state, updates

    def _answer_updates(self, state: str, text: str) -> dict:
        clipped = text[:500]
        if state == INTAKE:
            return {"feeling_label": text[:80]}
        if state == LOCATE:
            updates: dict = {"body_note": clipped}
            intensity = parse_intensity(text)
            if intensity is not None:
                updates["intensity_before"] = intensity
            return updates
        if state == ALLOW:
            return {"allow_answer": clipped}
        if state == COULD:
            return {"could_answer": clipped}
        if state == WOULD:
            return {"would_answer": clipped}
        if state == WHEN:
            return {"when_answer": clipped}
        if state == REFLECT:
            updates = {"reflection": text[:2000]}
            intensity = parse_intensity(text)
            if intensity is not None:
                updates["intensity_after"] = intensity
            return updates
        return {}

    def _ask(self, state: str) -> str:
        template = QUESTIONS[state]
        try:
            phrased = self.phraser.phrase(template, state)
        except Exception:
            safe_log("phrase_fallback", state=state, reason="exception", ok=0)
            return template
        if not is_safe_single_question(phrased):
            safe_log("phrase_fallback", state=state, reason="guard", ok=0)
            return template
        return phrased.strip()

    def _stop_for_safety(self, conn, session: dict) -> dict:
        message = support_message()
        self._update(
            conn,
            session["id"],
            {
                "state": SAFETY_STOP,
                "previous_state": None,
                "safety_stopped": 1,
                "turn_count": int(session["turn_count"]) + 1,
            },
        )
        self._add_turn(conn, session["id"], SAFETY_STOP, "assistant", message, now_iso())
        write_audit(conn, "sedona.safety_stop", "sedona", session["id"], 1)
        safe_log("safety_stop", entity="sedona", entity_id=session["id"], ok=1)
        return self._view(conn, session["id"], message)

    def _add_turn(self, conn, session_id: int, state: str, role: str, content: str, moment: str) -> None:
        conn.execute(
            """
            INSERT INTO sedona_turns(session_id, state, role, content, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (session_id, state, role, content, moment),
        )

    def _update(self, conn, session_id: int, fields: dict) -> None:
        keys = [key for key in fields if key in _UPDATABLE]
        if not keys:
            conn.execute(
                "UPDATE sedona_sessions SET updated_at = ? WHERE id = ?",
                (now_iso(), session_id),
            )
            return
        assignments = ", ".join(f"{key} = ?" for key in keys)
        values = [fields[key] for key in keys]
        values.extend([now_iso(), session_id])
        conn.execute(
            f"UPDATE sedona_sessions SET {assignments}, updated_at = ? WHERE id = ?",
            values,
        )

    def _view(self, conn, session_id: int, message: str) -> dict:
        row = conn.execute(
            "SELECT * FROM sedona_sessions WHERE id = ?",
            (session_id,),
        ).fetchone()
        turns = conn.execute(
            """
            SELECT id, role, state, content, created_at
            FROM sedona_turns
            WHERE session_id = ?
            ORDER BY id
            """,
            (session_id,),
        ).fetchall()
        data = dict(row)
        data["assistant_message"] = message
        data["turns"] = [dict(item) for item in turns]
        data["ok"] = True
        return data
