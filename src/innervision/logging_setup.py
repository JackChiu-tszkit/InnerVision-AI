"""日志只记录事件名称和结果，避免日记正文进入调试日志。"""

from __future__ import annotations

import logging
import re
from pathlib import Path

LOGGER_NAME = "innervision"

# 这些字段即使被误传入日志，也会被过滤器抹掉。
_FORBIDDEN_ASSIGNMENT = re.compile(
    r"(item_[123]|reflection|user_text|content|description|detail|notes|"
    r"feeling|body_note|image_base64|payload|preview|transcript)=([^\s]+)"
)


class RedactFilter(logging.Filter):
    """抹掉形如 item_1=... 的敏感赋值，避免正文落盘。"""

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        redacted = _FORBIDDEN_ASSIGNMENT.sub(r"\1=[已省略]", message)
        if redacted != message:
            record.msg = redacted
            record.args = ()
        return True


def get_logger() -> logging.Logger:
    """返回应用日志器。调用方只应通过 safe_log 记录白名单字段。"""
    return logging.getLogger(LOGGER_NAME)


def silence_library_logs() -> None:
    """关闭第三方日志，避免工具参数里的日记正文被库本身打印出来。"""
    for name in ("fastmcp", "mcp", "httpx", "httpcore", "langchain", "langchain_ollama"):
        library_logger = logging.getLogger(name)
        library_logger.handlers.clear()
        library_logger.propagate = False
        library_logger.setLevel(logging.CRITICAL)


def configure_logging(log_dir: Path) -> None:
    """把日志写到数据目录。重复调用不会叠加多个文件句柄。"""
    silence_library_logs()
    log_dir.mkdir(parents=True, exist_ok=True)
    logger = get_logger()
    logger.setLevel(logging.INFO)
    logger.propagate = False
    target = (log_dir / "app.log").resolve()
    file_handlers = [handler for handler in logger.handlers if isinstance(handler, logging.FileHandler)]
    if any(Path(handler.baseFilename).resolve() == target for handler in file_handlers):
        return
    for handler in file_handlers:
        logger.removeHandler(handler)
        handler.close()
    handler = logging.FileHandler(target, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    handler.addFilter(RedactFilter())
    logger.addHandler(handler)


def safe_log(event: str, **fields: object) -> None:
    """记录不含正文的审计事件。未知或敏感字段会被直接丢弃。"""
    allowed = {"action", "entity", "entity_id", "ok", "tool", "reason", "intent", "state"}
    clean = {key: fields[key] for key in sorted(fields) if key in allowed}
    parts = [f"{key}={clean[key]}" for key in clean]
    get_logger().info("%s %s", event, " ".join(parts))
