"""测试夹具。每个测试使用独立的数据目录，并默认关闭在线模型。"""

from __future__ import annotations

from datetime import date

import pytest

from innervision.config import load_settings
from innervision.container import Container, build_container


class FixedClock:
    """把“今天”固定在 2026-11-04，方便断言周一起点。"""

    def today(self) -> date:
        return date(2026, 11, 4)


@pytest.fixture
def container(tmp_path, monkeypatch) -> Container:
    monkeypatch.setenv("INNERVISION_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("INNERVISION_ENABLE_LLM", "0")
    built = build_container(load_settings())
    built.orchestrator.clock = FixedClock()
    return built
