"""感恩日记：一天三件事，并生成不含推断承诺的周回顾。"""

from __future__ import annotations

from datetime import date

from innervision.auth import AuthorizationService
from innervision.db import Database
from innervision.models import GratitudeCreate, GratitudeDelete
from innervision.services.common import as_dict, write_audit
from innervision.timeutil import date_to_iso, monday_of, now_iso, parse_date, week_end


class GratitudeService:
    """保存和读取感恩记录。所有修改都要先通过授权核销。"""

    def __init__(self, db: Database, auth: AuthorizationService) -> None:
        self.db = db
        self.auth = auth

    def propose_create(self, payload: dict) -> str:
        """校验三件感恩事项，并生成一条尚未写入的确认请求。"""
        data = GratitudeCreate.model_validate(payload)
        return self.auth.propose("gratitude.create", data.model_dump())

    def propose_delete(self, entry_id: int) -> str:
        """提出删除某一天的记录，等待用户确认。"""
        data = GratitudeDelete(entry_id=entry_id)
        return self.auth.propose("gratitude.delete", data.model_dump())

    def save_authorized(self, token: str) -> dict:
        """核销授权后保存。同一天重复保存会覆盖当天内容。"""
        with self.db.session() as conn:
            payload = self.auth.consume(conn, token, "gratitude.create")
            data = GratitudeCreate.model_validate(payload)
            moment = now_iso()
            conn.execute(
                """
                INSERT INTO gratitude_entries(
                    entry_date, item_1, item_2, item_3, reflection, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(entry_date) DO UPDATE SET
                    item_1 = excluded.item_1,
                    item_2 = excluded.item_2,
                    item_3 = excluded.item_3,
                    reflection = excluded.reflection,
                    updated_at = excluded.updated_at
                """,
                (
                    data.entry_date,
                    data.item_1,
                    data.item_2,
                    data.item_3,
                    data.reflection,
                    moment,
                    moment,
                ),
            )
            row = conn.execute(
                "SELECT id FROM gratitude_entries WHERE entry_date = ?",
                (data.entry_date,),
            ).fetchone()
            entry_id = int(row["id"])
            write_audit(conn, "gratitude.create", "gratitude", entry_id, 1)
        return {"ok": True, "id": entry_id, "entry_date": data.entry_date}

    def delete_authorized(self, token: str) -> dict:
        """核销授权后删除一条感恩记录。"""
        with self.db.session() as conn:
            payload = self.auth.consume(conn, token, "gratitude.delete")
            data = GratitudeDelete.model_validate(payload)
            cursor = conn.execute(
                "DELETE FROM gratitude_entries WHERE id = ?",
                (data.entry_id,),
            )
            if cursor.rowcount != 1:
                raise ValueError("没有找到这条感恩记录")
            write_audit(conn, "gratitude.delete", "gratitude", data.entry_id, 1)
        return {"ok": True, "deleted": data.entry_id}

    def list_recent(self, limit: int = 20) -> list[dict]:
        """按日期倒序读取最近的感恩记录。"""
        safe_limit = min(max(int(limit), 1), 100)
        with self.db.session() as conn:
            rows = conn.execute(
                """
                SELECT * FROM gratitude_entries
                ORDER BY entry_date DESC
                LIMIT ?
                """,
                (safe_limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_by_date(self, entry_date: str) -> dict | None:
        """读取某一天的记录。没有记录时返回空。"""
        parse_date(entry_date)
        with self.db.session() as conn:
            row = conn.execute(
                "SELECT * FROM gratitude_entries WHERE entry_date = ?",
                (entry_date,),
            ).fetchone()
        return as_dict(row)

    def weekly_review(self, week_start: str) -> dict:
        """汇总一周的原文。这里只计数和回显，不推断人生主题，也不承诺改变。"""
        start = monday_of(parse_date(week_start))
        end = week_end(start)
        with self.db.session() as conn:
            rows = conn.execute(
                """
                SELECT * FROM gratitude_entries
                WHERE entry_date >= ? AND entry_date <= ?
                ORDER BY entry_date
                """,
                (date_to_iso(start), date_to_iso(end)),
            ).fetchall()
        items: list[str] = []
        entries = []
        for row in rows:
            item = dict(row)
            entries.append(item)
            items.extend([item["item_1"], item["item_2"], item["item_3"]])
        if entries:
            narrative = (
                f"这一周（{start.isoformat()} 至 {end.isoformat()}）"
                f"你记录了 {len(entries)} 天感恩，共 {len(items)} 条。这些是你写下的内容。"
            )
        else:
            narrative = f"这一周（{start.isoformat()} 至 {end.isoformat()}）还没有感恩记录。"
        return {
            "ok": True,
            "week_start": start.isoformat(),
            "week_end": end.isoformat(),
            "entry_count": len(entries),
            "items": items,
            "entries": entries,
            "narrative": narrative,
        }
