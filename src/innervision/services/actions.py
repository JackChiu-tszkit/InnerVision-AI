"""行动任务。P1 表示紧急且重要，P2 表示为以后积累。"""

from __future__ import annotations

from datetime import date

from innervision.auth import AuthorizationService
from innervision.db import Database
from innervision.models import ActionCreate, ActionIdentity, ActionLink, WeeklyNote
from innervision.services.common import as_dict, write_audit
from innervision.timeutil import date_to_iso, monday_of, now_iso, parse_date, week_end


class ActionService:
    """把愿景落成可以勾选的行动，并做每周数量回顾。"""

    def __init__(self, db: Database, auth: AuthorizationService) -> None:
        self.db = db
        self.auth = auth

    def propose_create(self, payload: dict) -> str:
        """校验行动内容后生成待确认请求。"""
        data = ActionCreate.model_validate(payload)
        return self.auth.propose("action.create", data.model_dump())

    def propose_complete(self, task_id: int) -> str:
        """提出把一项行动标为完成。"""
        data = ActionIdentity(task_id=task_id)
        return self.auth.propose("action.complete", data.model_dump())

    def propose_reopen(self, task_id: int) -> str:
        """提出重新打开一项行动。"""
        data = ActionIdentity(task_id=task_id)
        return self.auth.propose("action.reopen", data.model_dump())

    def propose_link(self, task_id: int, vision_id: int) -> str:
        """提出把行动关联到一张愿景卡片。"""
        data = ActionLink(task_id=task_id, vision_id=vision_id)
        return self.auth.propose("action.link", data.model_dump())

    def propose_weekly_note(self, week_start: str, notes: str) -> str:
        """提出保存一周的复盘备注。"""
        data = WeeklyNote(week_start=week_start, notes=notes)
        return self.auth.propose("action.weekly_note", data.model_dump())

    def save_authorized(self, token: str) -> dict:
        """核销授权后创建行动。如果指定了愿景，必须是已存在的卡片。"""
        with self.db.session() as conn:
            payload = self.auth.consume(conn, token, "action.create")
            data = ActionCreate.model_validate(payload)
            if data.vision_id is not None:
                found = conn.execute(
                    "SELECT id FROM vision_cards WHERE id = ?",
                    (data.vision_id,),
                ).fetchone()
                if found is None:
                    raise ValueError("关联的愿景不存在")
            cursor = conn.execute(
                """
                INSERT INTO action_tasks(title, detail, priority, status, vision_id, created_at)
                VALUES (?, ?, ?, 'open', ?, ?)
                """,
                (data.title, data.detail, data.priority, data.vision_id, now_iso()),
            )
            task_id = int(cursor.lastrowid)
            write_audit(conn, "action.create", "action", task_id, 1)
        return {"ok": True, "id": task_id}

    def complete_authorized(self, token: str) -> dict:
        """把行动标为完成。"""
        return self._set_status(token, "action.complete", done=True)

    def reopen_authorized(self, token: str) -> dict:
        """把行动改回未完成。"""
        return self._set_status(token, "action.reopen", done=False)

    def link_authorized(self, token: str) -> dict:
        """把已有行动关联到愿景卡片。"""
        with self.db.session() as conn:
            payload = self.auth.consume(conn, token, "action.link")
            data = ActionLink.model_validate(payload)
            task = conn.execute(
                "SELECT id FROM action_tasks WHERE id = ?",
                (data.task_id,),
            ).fetchone()
            vision = conn.execute(
                "SELECT id FROM vision_cards WHERE id = ?",
                (data.vision_id,),
            ).fetchone()
            if task is None or vision is None:
                raise ValueError("行动或愿景不存在")
            conn.execute(
                "UPDATE action_tasks SET vision_id = ? WHERE id = ?",
                (data.vision_id, data.task_id),
            )
            write_audit(conn, "action.link", "action", data.task_id, 1)
        return {"ok": True, "id": data.task_id, "vision_id": data.vision_id}

    def save_note_authorized(self, token: str) -> dict:
        """保存某一周的行动复盘备注。"""
        with self.db.session() as conn:
            payload = self.auth.consume(conn, token, "action.weekly_note")
            data = WeeklyNote.model_validate(payload)
            moment = now_iso()
            conn.execute(
                """
                INSERT INTO weekly_reviews(week_start, notes, created_at, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(week_start) DO UPDATE SET
                    notes = excluded.notes,
                    updated_at = excluded.updated_at
                """,
                (data.week_start, data.notes, moment, moment),
            )
            write_audit(conn, "action.weekly_note", "weekly_review", None, 1)
        return {"ok": True, "week_start": data.week_start}

    def list_tasks(self, status: str | None = None, priority: str | None = None) -> list[dict]:
        """按优先级读取行动。筛选值只接受已知状态。"""
        if status not in {None, "open", "done"}:
            raise ValueError("未知的完成状态")
        if priority not in {None, "P1", "P2"}:
            raise ValueError("未知的优先级")
        query = "SELECT * FROM action_tasks WHERE 1 = 1"
        args: list[str] = []
        if status:
            query += " AND status = ?"
            args.append(status)
        if priority:
            query += " AND priority = ?"
            args.append(priority)
        query += " ORDER BY CASE priority WHEN 'P1' THEN 0 ELSE 1 END, id DESC"
        with self.db.session() as conn:
            rows = conn.execute(query, args).fetchall()
        return [dict(row) for row in rows]

    def weekly_review(self, week_start: str) -> dict:
        """统计一周内完成的行动，以及当前仍开放的行动。"""
        start = monday_of(parse_date(week_start))
        end = week_end(start)
        tasks = self.list_tasks()
        done = []
        for task in tasks:
            completed = _date_prefix(task.get("completed_at"))
            if completed is not None and start <= completed <= end:
                done.append(task)
        open_tasks = [task for task in tasks if task["status"] == "open"]
        p1_done = sum(1 for task in done if task["priority"] == "P1")
        p2_done = sum(1 for task in done if task["priority"] == "P2")
        note = self._note(date_to_iso(start))
        narrative = (
            f"本周（{start.isoformat()} 至 {end.isoformat()}）完成 {len(done)} 项，"
            f"目前仍有 {len(open_tasks)} 项未完成。"
            f"本周完成的 P1 有 {p1_done} 项，P2 有 {p2_done} 项。"
        )
        return {
            "ok": True,
            "week_start": start.isoformat(),
            "week_end": end.isoformat(),
            "done_count": len(done),
            "open_count": len(open_tasks),
            "p1_done": p1_done,
            "p2_done": p2_done,
            "done": done,
            "open_tasks": open_tasks,
            "notes": note,
            "narrative": narrative,
        }

    def suggest_from_vision(self, vision: dict) -> list[dict]:
        """根据未完成里程碑给出行动草稿。这里只建议，不保存。"""
        drafts = []
        for milestone in vision.get("milestones", []):
            if milestone.get("done"):
                continue
            drafts.append(
                {
                    "title": milestone["title"],
                    "detail": f"来自愿景「{vision['title']}」的里程碑",
                    "priority": "P2",
                    "vision_id": vision["id"],
                }
            )
        return drafts

    def _set_status(self, token: str, action: str, *, done: bool) -> dict:
        with self.db.session() as conn:
            payload = self.auth.consume(conn, token, action)
            data = ActionIdentity.model_validate(payload)
            status = "done" if done else "open"
            completed_at = now_iso() if done else None
            cursor = conn.execute(
                """
                UPDATE action_tasks
                SET status = ?, completed_at = ?
                WHERE id = ?
                """,
                (status, completed_at, data.task_id),
            )
            if cursor.rowcount != 1:
                raise ValueError("没有找到这项行动")
            write_audit(conn, action, "action", data.task_id, 1)
        return {"ok": True, "id": data.task_id, "status": status}

    def _note(self, week_start: str) -> str:
        with self.db.session() as conn:
            row = conn.execute(
                "SELECT notes FROM weekly_reviews WHERE week_start = ?",
                (week_start,),
            ).fetchone()
        found = as_dict(row)
        return "" if found is None else found["notes"]


def _date_prefix(value: str | None) -> date | None:
    if not value:
        return None
    return parse_date(value[:10])
