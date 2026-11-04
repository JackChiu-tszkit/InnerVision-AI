import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import streamlit as st

from innervision.config import load_settings
from innervision.container import build_container
from innervision.ui.common import apply_theme
from innervision.ui.pages import render_page


def main() -> None:
    """启动本地界面。服务只准备在本机回环地址使用。"""
    st.set_page_config(page_title="InnerVision AI", page_icon="🌿", layout="wide")
    apply_theme()
    settings = load_settings()
    container = _container()
    st.sidebar.title("InnerVision")
    page = st.sidebar.radio(
        "功能",
        [
            "今日",
            "每日仪式",
            "感恩日记",
            "愿景板",
            "释放练习",
            "行动教练",
            "趋势",
            "成长报告",
            "对话",
            "数据与隐私",
        ],
    )
    st.sidebar.caption(f"数据目录在本机。界面与 MCP 只使用 {settings.streamlit_host}。")
    if st.session_state.get("pending_write"):
        st.sidebar.warning("有一项写入正在等待确认。")
    render_page(page, container)


@st.cache_resource
def _container():
    """缓存本机服务，避免每次点击都重新打开数据库结构。"""
    return build_container(load_settings())


if __name__ == "__main__":
    main()
