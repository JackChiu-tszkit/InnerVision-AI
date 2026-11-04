"""写入授权。未确认、过期或被篡改的请求不能进入业务写入。"""

from __future__ import annotations

import hashlib
import json
import secrets
import sqlite3
from typing import Any

from innervision.db import Database
from innervision.logging_setup import safe_log
from innervision.timeutil import now_iso, shift_iso


class AuthorizationError(Exception):
    """授权失败时使用固定说明，避免异常文本带出日记内容。"""

    def __init__(self, message: str = "写入尚未得到用户确认，或授权已失效。") -> None:
        super().__init__(message)


def canonical_payload(payload: dict[str, Any]) -> str:
    """生成稳定的 JSON，使确认前后可以核对是不是同一份内容。"""
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def payload_hash(payload: dict[str, Any]) -> str:
    """计算授权内容的摘要。摘要可以记日志，原文不行。"""
    return hashlib.sha256(canonical_payload(payload).encode("utf-8")).hexdigest()


class AuthorizationService:
    """签发、确认并核销一次性写入令牌。"""

    def __init__(self, db: Database) -> None:
        self.db = db

    def propose(self, action: str, payload: dict[str, Any], *, ttl_seconds: int = 1800) -> str:
        """保存待确认内容并返回令牌。此时还没有写入业务表。"""
        token = secrets.token_urlsafe(24)
        body = canonical_payload(payload)
        with self.db.session() as conn:
            conn.execute(
                """
                INSERT INTO pending_writes(
                    token, action, payload_hash, payload_json, confirmed, consumed, created_at, expires_at
                ) VALUES (?, ?, ?, ?, 0, 0, ?, ?)
                """,
                (token, action, payload_hash(payload), body, now_iso(), shift_iso(ttl_seconds)),
            )
        safe_log("write_proposed", action=action, ok=1)
        return token

    def confirm(self, token: str) -> None:
        """标记用户已经点过确认。只有界面层可以调用。"""
        with self.db.session() as conn:
            cursor = conn.execute(
                """
                UPDATE pending_writes
                SET confirmed = 1
                WHERE token = ? AND consumed = 0 AND confirmed = 0 AND expires_at > ?
                """,
                (token, now_iso()),
            )
            if cursor.rowcount != 1:
                safe_log("write_confirm_denied", ok=0)
                raise AuthorizationError()
        safe_log("write_confirmed", ok=1)

    def is_confirmed(self, token: str) -> bool:
        """查看令牌是否已确认且仍可写入。不返回正文。"""
        with self.db.session() as conn:
            row = conn.execute(
                """
                SELECT confirmed, consumed, expires_at
                FROM pending_writes
                WHERE token = ?
                """,
                (token,),
            ).fetchone()
        if row is None or row["consumed"] or row["expires_at"] <= now_iso():
            return False
        return bool(row["confirmed"])

    def cancel(self, token: str) -> None:
        """取消尚未核销的写入，并清掉暂存的正文。"""
        with self.db.session() as conn:
            conn.execute(
                """
                UPDATE pending_writes
                SET consumed = 1, payload_json = '{"cleared":true}', payload_hash = ''
                WHERE token = ? AND consumed = 0
                """,
                (token,),
            )
        safe_log("write_cancelled", ok=1)

    def peek(self, token: str, expected_action: str) -> dict[str, Any]:
        """查看已确认的内容，但不核销。用于进入同一事务前做校验。"""
        with self.db.session() as conn:
            return self._valid_payload(conn, token, expected_action)

    def consume(self, conn: sqlite3.Connection, token: str, expected_action: str) -> dict[str, Any]:
        """在业务事务内核销令牌。成功写入后不再保留第二份正文。"""
        payload = self._valid_payload(conn, token, expected_action)
        cursor = conn.execute(
            """
            UPDATE pending_writes
            SET consumed = 1, payload_json = '{"cleared":true}', payload_hash = ''
            WHERE token = ? AND consumed = 0
            """,
            (token,),
        )
        if cursor.rowcount != 1:
            raise AuthorizationError()
        return payload

    def _valid_payload(
        self, conn: sqlite3.Connection, token: str, expected_action: str
    ) -> dict[str, Any]:
        row = conn.execute(
            "SELECT * FROM pending_writes WHERE token = ?",
            (token,),
        ).fetchone()
        if row is None or row["consumed"] or not row["confirmed"]:
            raise AuthorizationError()
        if row["expires_at"] <= now_iso() or row["action"] != expected_action:
            raise AuthorizationError()
        payload = json.loads(row["payload_json"])
        if not isinstance(payload, dict) or payload_hash(payload) != row["payload_hash"]:
            raise AuthorizationError()
        return payload
