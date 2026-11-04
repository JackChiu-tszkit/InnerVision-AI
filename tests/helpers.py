"""测试里共用的确认写入。"""

from __future__ import annotations

from innervision.container import Container


def commit(container: Container, token: str) -> dict:
    """模拟用户点击确认后的核销。"""
    container.auth.confirm(token)
    return container.dispatcher.commit(token)
