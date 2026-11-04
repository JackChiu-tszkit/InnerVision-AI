"""界面共用的确认与错误显示。"""

from __future__ import annotations

import json

import streamlit as st
from pydantic import ValidationError

from innervision.auth import AuthorizationError
from innervision.container import Container

_HIDDEN_PREVIEW = {"image_base64", "image_ext", "kind"}


def apply_theme() -> None:
    """设置页面宽度。样式是固定文本，不拼用户内容。"""
    st.markdown(
        """
        <style>
        .block-container {padding-top: 1.2rem; max-width: 980px;}
        </style>
        """,
        unsafe_allow_html=True,
    )


def show_error(exc: Exception) -> None:
    """只显示固定或很短的说明，避免把校验原文回显出来。"""
    if isinstance(exc, AuthorizationError):
        st.error(str(exc))
    elif isinstance(exc, ValidationError):
        st.error("内容未通过校验，请检查必填项和长度。")
    elif isinstance(exc, ValueError) and len(str(exc)) <= 80:
        st.error(str(exc))
    else:
        st.error("操作无法完成。")


def user_commit(container: Container, token: str) -> dict:
    """用户点击确认后核销令牌。已经确认过但上次没写完时可以重试。"""
    try:
        container.auth.confirm(token)
    except AuthorizationError:
        if not container.auth.is_confirmed(token):
            raise
    return container.dispatcher.commit(token)


def stage_pending(token: str, action: str, preview: dict, title: str) -> None:
    """把待确认内容放进会话。此时业务表还没有变化。"""
    st.session_state["pending_write"] = {
        "token": token,
        "action": action,
        "preview": preview,
        "title": title,
    }


def render_pending(container: Container, actions: set[str]) -> None:
    """显示确认面板。取消或成功后都会清掉暂存内容。"""
    pending = st.session_state.get("pending_write")
    if not pending or pending.get("action") not in actions:
        return
    st.info(pending["title"])
    for key, value in pending.get("preview", {}).items():
        if key in _HIDDEN_PREVIEW:
            continue
        st.caption(str(key))
        if isinstance(value, list):
            st.text("\n".join(str(item) for item in value))
        elif isinstance(value, dict):
            st.text(json.dumps(value, ensure_ascii=False, indent=2))
        else:
            st.text("" if value is None else str(value))
    agree, cancel = st.columns(2)
    token = pending["token"]
    if agree.button("确认写入", type="primary", key=f"commit-{token[:8]}"):
        try:
            user_commit(container, token)
        except Exception as exc:
            show_error(exc)
        else:
            st.session_state.pop("pending_write", None)
            st.session_state["flash"] = "已保存在本机。"
            st.rerun()
    if cancel.button("取消", key=f"cancel-{token[:8]}"):
        container.auth.cancel(token)
        st.session_state.pop("pending_write", None)
        st.rerun()
