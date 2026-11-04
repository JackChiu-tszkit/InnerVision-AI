"""把已确认的令牌交给对应的业务服务。"""

from __future__ import annotations

from collections.abc import Callable

from innervision.auth import AuthorizationError, AuthorizationService
from innervision.logging_setup import safe_log
from innervision.timeutil import now_iso


class Dispatcher:
    """按授权里的动作名称调用业务写入。它不负责代替用户确认。"""

    def __init__(self, auth: AuthorizationService, routes: dict[str, Callable[[str], dict]]) -> None:
        self.auth = auth
        self.routes = routes

    def commit(self, token: str) -> dict:
        """核销前再次确认令牌有效，然后执行对应的业务写入。"""
        action = self._action_of(token)
        handler = self.routes.get(action)
        if handler is None:
            raise AuthorizationError()
        result = handler(token)
        safe_log("write_committed", action=action, ok=1)
        return result

    def _action_of(self, token: str) -> str:
        with self.auth.db.session() as conn:
            row = conn.execute(
                """
                SELECT action, confirmed, consumed, expires_at
                FROM pending_writes
                WHERE token = ?
                """,
                (token,),
            ).fetchone()
        if row is None or row["consumed"] or not row["confirmed"] or row["expires_at"] <= now_iso():
            raise AuthorizationError()
        return str(row["action"])
