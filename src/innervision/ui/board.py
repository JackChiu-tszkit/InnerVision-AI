"""愿景板拖动布局。松手或点击移动都会经过授权后写入。"""

from __future__ import annotations

import base64
from pathlib import Path

import streamlit as st

from innervision.container import Container
from innervision.ui.common import show_error, user_commit

_COMPONENT_DIR = Path(__file__).resolve().parent / "vision_board"
_MIME = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
}
_THUMBNAIL_LIMIT = 120_000


def save_order(container: Container, ordered_ids: list[int]) -> None:
    """用户拖动或点击移动后，立刻核销并保存新顺序。"""
    token = container.vision.propose_layout(ordered_ids)
    user_commit(container, token)
    st.session_state["board_generation"] = int(st.session_state.get("board_generation", 0)) + 1
    st.session_state["flash"] = "愿景板顺序已保存在本机。"
    st.rerun()


def render_board(container: Container) -> None:
    """画出可拖动的图片板。少于两张卡片时不启用拖动。"""
    if st.session_state.get("board_error"):
        st.error(st.session_state.pop("board_error"))
    cards = container.vision.list_cards()
    if len(cards) < 2:
        return
    st.caption("拖动卡片，或使用前移、后移。松手或点击后，新顺序会写进本机数据库。")
    generation = int(st.session_state.get("board_generation", 0))
    value = _widget()(cards=_payload(container, cards), key=f"vision-board-{generation}", default=None)
    proposed = _ids(value)
    current = [int(card["id"]) for card in cards]
    if proposed is None or proposed == current:
        return
    try:
        save_order(container, proposed)
    except Exception as exc:
        st.session_state["board_generation"] = generation + 1
        if isinstance(exc, ValueError) and len(str(exc)) <= 80:
            st.session_state["board_error"] = str(exc)
        else:
            st.session_state["board_error"] = "愿景板顺序没有保存。"
        show_error(exc)
        st.rerun()


def _payload(container: Container, cards: list[dict]) -> list[dict]:
    payload = []
    for card in cards:
        payload.append(
            {
                "id": int(card["id"]),
                "title": card["title"],
                "category": card["category"],
                "image": _thumbnail(container, card.get("image_name")),
            }
        )
    return payload


def _thumbnail(container: Container, image_name: str | None) -> str:
    path = container.vision.image_file(image_name)
    if path is None or path.stat().st_size > _THUMBNAIL_LIMIT:
        return ""
    mime = _MIME.get(path.suffix.lower(), "")
    if not mime:
        return ""
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{encoded}"


def _ids(value: object) -> list[int] | None:
    if not isinstance(value, dict):
        return None
    raw = value.get("ids")
    if not isinstance(raw, list) or not raw:
        return None
    try:
        return [int(item) for item in raw]
    except (TypeError, ValueError):
        return None


@st.cache_resource
def _widget():
    import streamlit.components.v1 as components

    return components.declare_component("innervision_vision_board", path=str(_COMPONENT_DIR))
