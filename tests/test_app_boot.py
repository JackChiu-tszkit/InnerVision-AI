"""确认 Streamlit 入口能够在关闭模型时完成首次渲染。"""

from __future__ import annotations

from pathlib import Path

from streamlit.testing.v1 import AppTest

ROOT = Path(__file__).resolve().parents[1]


def test_app_boots(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("INNERVISION_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("INNERVISION_ENABLE_LLM", "0")
    app = AppTest.from_file(str(ROOT / "app.py"))
    app.run(timeout=30)
    assert not app.exception
    assert app.title[0].value == "InnerVision AI"
