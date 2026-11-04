"""服务层共享的小工具。"""

from __future__ import annotations

import sqlite3

from innervision.logging_setup import safe_log
from innervision.timeutil import now_iso


def write_audit(
    conn: sqlite3.Connection,
    action: str,
    entity: str,
    entity_id: int | None,
    ok: int = 1,
) -> None:
    """写入审计表和安全日志。两者都不保存日记正文。"""
    conn.execute(
        """
        INSERT INTO audit_log(action, entity, entity_id, ok, created_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (action, entity, entity_id, ok, now_iso()),
    )
    safe_log("audit", action=action, entity=entity, entity_id=entity_id, ok=ok)


def as_dict(row: sqlite3.Row | None) -> dict | None:
    """把查询行转成普通字典，方便返回给界面。"""
    if row is None:
        return None
    return dict(row)
