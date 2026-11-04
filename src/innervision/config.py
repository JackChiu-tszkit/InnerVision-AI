"""运行配置。监听地址固定为本机，忽略把服务暴露到局域网的设置。"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse


LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}


@dataclass(frozen=True)
class Settings:
    """应用路径与本地模型设置。"""

    data_dir: Path
    db_path: Path
    image_dir: Path
    export_dir: Path
    backup_dir: Path
    log_dir: Path
    skills_dir: Path
    ollama_base_url: str
    ollama_model: str
    enable_llm: bool
    mcp_host: str
    mcp_port: int
    streamlit_host: str


def assert_loopback_url(url: str) -> str:
    """只接受指向本机的 Ollama 地址，避免把记录发到其他机器。"""
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if parsed.scheme not in {"http", "https"} or host not in LOCAL_HOSTS:
        raise ValueError("Ollama 地址必须指向本机")
    return url.rstrip("/")


def load_settings() -> Settings:
    """从环境变量读取数据目录和模型名称。主机名始终是 127.0.0.1。"""
    data_dir = Path(
        os.environ.get("INNERVISION_DATA_DIR", str(Path.home() / ".innervision"))
    ).expanduser()
    project_root = Path(__file__).resolve().parents[2]
    ollama_url = assert_loopback_url(
        os.environ.get("INNERVISION_OLLAMA_URL", "http://127.0.0.1:11434")
    )
    enable_raw = os.environ.get("INNERVISION_ENABLE_LLM", "1").strip().lower()
    return Settings(
        data_dir=data_dir,
        db_path=data_dir / "innervision.db",
        image_dir=data_dir / "images",
        export_dir=data_dir / "exports",
        backup_dir=data_dir / "backups",
        log_dir=data_dir / "logs",
        skills_dir=project_root / "skills",
        ollama_base_url=ollama_url,
        ollama_model=os.environ.get("INNERVISION_OLLAMA_MODEL", "qwen2.5:7b"),
        enable_llm=enable_raw not in {"0", "false", "no", "off"},
        mcp_host="127.0.0.1",
        mcp_port=int(os.environ.get("INNERVISION_MCP_PORT", "8765")),
        streamlit_host="127.0.0.1",
    )
