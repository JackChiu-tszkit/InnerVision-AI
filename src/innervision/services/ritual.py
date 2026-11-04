"""晨间和晚间仪式。步骤由状态机决定，每次只给一个问题。"""

from __future__ import annotations

from innervision.auth import AuthorizationService
from innervision.db import Database
from innervision.logging_setup import safe_log
from innervision.models import RitualResume, RitualStart, RitualStep
from innervision.safety import is_crisis_text, support_message
from innervision.services.common import write_audit
from innervision.services.sedona import parse_command
from innervision.timeutil import SystemClock, now_iso

MORNING = ["arrive", "gratitude", "focus"]
EVENING = ["review", "ease"]
PAUSED = "paused"
COMPLETED = "completed"
SAFETY_STOP = "safety_stop"
CLOSED = {COMPLETED, SAFETY_STOP}
MAX_TURNS = 24

QUESTIONS = {
    "arrive": "今天开始时，你想给自己哪一句提醒？",
    "gratitude": "眼前有没有一件已经不错的小事？",
    "focus": "今天如果只照顾一件重要的事，它会是什么？",
    "review": "今天有哪一件事，你愿意为自己记一笔？",
    "ease": "如果用 0 到 10 表示此刻的轻松程度，大概是多少？",
}

STATE_LABELS = {
    "arrive": "给今天一句提醒",
    "gratitude": "看见一件小事",
    "focus": "今天的一件事",
    "review": "记下今天",
    "ease": "此刻的轻松程度",
    PAUSED: "已暂停",
    COMPLETED: "已结束",
    SAFETY_STOP: "已停止",
}

PAUSE_MESSAGE = "已经暂停。想继续时可以点击继续，现在不需要做任何事。"
END_MESSAGE = "仪式已经结束。你写下的内容只留在这台电脑上。"
CLOSE_MESSAGE = "仪式先到这里。这些话只留在这台电脑上，你不需要有任何改变。如果还想看看情绪，可以稍后自己打开释放练习。"
LENGTH_MESSAGE = "这次仪式的回合已经比较长，我们先在这里停下来。记录只留在本机。"

_UPDATABLE = {"state", "previous_state", "safety_stopped", "turn_count"}


class RitualService:
    """管理一天的晨间或晚间仪式。用户可以暂停、跳过或结束。"""

    def __init__(self, db: Database, auth: AuthorizationService, clock: SystemClock | None = None) -> None:
        self.db = db
        self.auth = auth
        self.clock = clock or SystemClock()

    def propose_start(self, kind: str) -> str:
        """提出开始一次仪式。已经有未结束的仪式时不会生成请求。"""
        data = RitualStart(kind=kind)
        if self.get_open() is not None:
            raise ValueError("已经有一次仪式还没结束")
        return self.auth.propose("ritual.start", data.model_dump())

    def propose_step(self, session_id: int, user_text: str) -> str:
        """提出保存一步回答。真正写入要等用户确认。"""
        data = RitualStep(session_id=session_id, user_text=user_text)
        return self.auth.propose("ritual.step", data.model_dump())

    def propose_resume(self, session_id: int) -> str:
        """提出从暂停处继续。"""
        data = RitualResume(session_id=session_id)
        return self.auth.propose("ritual.resume", data.model_dump())

    def start_authorized(self, token: str) -> dict:
        """核销授权后开始仪式，并只提出第一个问题。"""
        if self.get_open() is not None:
            raise ValueError("已经有一次仪式还没结束")
        with self.db.session() as conn:
            payload = self.auth.consume(conn, token, "ritual.start")
            data = RitualStart.model_validate(payload)
            state = _flow(data.kind)[0]
            message = QUESTIONS[state]
            moment = now_iso()
            cursor = conn.execute(
                """
                INSERT INTO ritual_sessions(
                    kind, ritual_date, state, safety_stopped, turn_count, created_at, updated_at
                ) VALUES (?, ?, ?, 0, 1, ?, ?)
                """,
                (data.kind, self.clock.today().isoformat(), state, moment, moment),
            )
            session_id = int(cursor.lastrowid)
            self._add_turn(conn, session_id, state, "assistant", message, moment)
            write_audit(conn, "ritual.start", "ritual", session_id, 1)
            return self._view(conn, session_id, message)

    def step_authorized(self, token: str) -> dict:
        """核销授权后前进一步。安全风险会立刻停止，且不保存原文。"""
        with self.db.session() as conn:
            payload = self.auth.consume(conn, token, "ritual.step")
            data = RitualStep.model_validate(payload)
            row = conn.execute(
                "SELECT * FROM ritual_sessions WHERE id = ?",
                (data.session_id,),
            ).fetchone()
            if row is None:
                raise ValueError("没有找到这次仪式")
            session = dict(row)
            if session["state"] in CLOSED:
                raise ValueError("这次仪式已经结束")
            if is_crisis_text(data.user_text):
                return self._stop_for_safety(conn, session)
            message, new_state, updates = self._transition(session, data.user_text)
            updates["state"] = new_state
            updates["turn_count"] = int(session["turn_count"]) + 2
            self._update(conn, data.session_id, updates)
            moment = now_iso()
            self._add_turn(conn, data.session_id, session["state"], "user", data.user_text, moment)
            self._add_turn(conn, data.session_id, new_state, "assistant", message, moment)
            write_audit(conn, "ritual.step", "ritual", data.session_id, 1)
            return self._view(conn, data.session_id, message)

    def resume_authorized(self, token: str) -> dict:
        """从暂停前的问题继续，并重新提出那一个问题。"""
        with self.db.session() as conn:
            payload = self.auth.consume(conn, token, "ritual.resume")
            data = RitualResume.model_validate(payload)
            row = conn.execute(
                "SELECT * FROM ritual_sessions WHERE id = ?",
                (data.session_id,),
            ).fetchone()
            if row is None:
                raise ValueError("没有找到这次仪式")
            session = dict(row)
            if session["state"] != PAUSED:
                raise ValueError("这次仪式不在暂停中")
            restored = session.get("previous_state") or _flow(session["kind"])[0]
            if restored not in QUESTIONS:
                restored = _flow(session["kind"])[0]
            message = QUESTIONS[restored]
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
            write_audit(conn, "ritual.resume", "ritual", data.session_id, 1)
            return self._view(conn, data.session_id, message)

    def stop_for_safety(self, session_id: int) -> None:
        """立即停止开放中的仪式。不接收、不保存用户原文。"""
        with self.db.session() as conn:
            row = conn.execute(
                "SELECT * FROM ritual_sessions WHERE id = ?",
                (session_id,),
            ).fetchone()
            if row is None or row["state"] in CLOSED:
                return
            self._stop_for_safety(conn, dict(row))

    def get_open(self) -> dict | None:
        """读取尚未结束的仪式，不附带回答正文。"""
        with self.db.session() as conn:
            row = conn.execute(
                """
                SELECT id, kind, ritual_date, state, safety_stopped
                FROM ritual_sessions
                WHERE state NOT IN ('completed', 'safety_stop')
                ORDER BY id DESC
                LIMIT 1
                """
            ).fetchone()
        return dict(row) if row else None

    def latest(self, kind: str, day: str) -> dict | None:
        """读取某一天最近一次仪式的状态，不附带回答正文。"""
        with self.db.session() as conn:
            row = conn.execute(
                """
                SELECT id, kind, ritual_date, state, safety_stopped
                FROM ritual_sessions
                WHERE kind = ? AND ritual_date = ?
                ORDER BY id DESC
                LIMIT 1
                """,
                (kind, day),
            ).fetchone()
        return dict(row) if row else None

    def get_detail(self, session_id: int) -> dict | None:
        """读取用户自己查看的仪式详情。"""
        with self.db.session() as conn:
            row = conn.execute(
                "SELECT * FROM ritual_sessions WHERE id = ?",
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
        kind = str(session["kind"])
        state = str(session["state"])
        if int(session["turn_count"]) >= MAX_TURNS and parse_command(text) != "end":
            return LENGTH_MESSAGE, COMPLETED, {"previous_state": None}
        command = parse_command(text)
        if state == PAUSED:
            if command == "end":
                return END_MESSAGE, COMPLETED, {"previous_state": None}
            if command == "pause":
                return PAUSE_MESSAGE, PAUSED, {}
            if command == "skip":
                restored = session.get("previous_state") or _flow(kind)[0]
                return _advance(kind, str(restored))
            raise ValueError("这次仪式还在暂停中")
        if command == "pause":
            return PAUSE_MESSAGE, PAUSED, {"previous_state": state}
        if command == "end":
            return END_MESSAGE, COMPLETED, {"previous_state": None}
        return _advance(kind, state)

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
        write_audit(conn, "ritual.safety_stop", "ritual", session["id"], 1)
        safe_log("safety_stop", entity="ritual", entity_id=session["id"], ok=1)
        return self._view(conn, session["id"], message)

    def _add_turn(self, conn, session_id: int, state: str, role: str, content: str, moment: str) -> None:
        conn.execute(
            """
            INSERT INTO ritual_turns(session_id, state, role, content, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (session_id, state, role, content, moment),
        )

    def _update(self, conn, session_id: int, fields: dict) -> None:
        keys = [key for key in fields if key in _UPDATABLE]
        if not keys:
            return
        assignments = ", ".join(f"{key} = ?" for key in keys)
        values = [fields[key] for key in keys]
        values.append(now_iso())
        values.append(session_id)
        conn.execute(
            f"UPDATE ritual_sessions SET {assignments}, updated_at = ? WHERE id = ?",
            values,
        )

    def _view(self, conn, session_id: int, message: str) -> dict:
        row = conn.execute(
            "SELECT * FROM ritual_sessions WHERE id = ?",
            (session_id,),
        ).fetchone()
        turns = conn.execute(
            """
            SELECT id, role, state, content, created_at
            FROM ritual_turns WHERE session_id = ? ORDER BY id
            """,
            (session_id,),
        ).fetchall()
        data = dict(row)
        data["assistant_message"] = message
        data["turns"] = [dict(item) for item in turns]
        data["ok"] = True
        return data


def _flow(kind: str) -> list[str]:
    if kind == "morning":
        return MORNING
    if kind == "evening":
        return EVENING
    raise ValueError("仪式类型无效")


def _advance(kind: str, state: str) -> tuple[str, str, dict]:
    flow = _flow(kind)
    if state not in flow:
        state = flow[0]
    index = flow.index(state)
    if index + 1 >= len(flow):
        return CLOSE_MESSAGE, COMPLETED, {"previous_state": None}
    nxt = flow[index + 1]
    return QUESTIONS[nxt], nxt, {"previous_state": None}
