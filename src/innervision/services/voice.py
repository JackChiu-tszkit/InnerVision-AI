"""可选的本机语音转写。没有本地模型时不录音，也不把音频发到网上。"""

from __future__ import annotations

import importlib.util
import tempfile
from collections.abc import Callable
from pathlib import Path

from innervision.config import Settings
from innervision.logging_setup import safe_log

_SUFFIXES = {".wav", ".webm", ".mp3", ".m4a", ".ogg", ".flac"}
_MODEL: dict[str, object] = {"obj": None}


class VoiceService:
    """把短录音转成文字。测试可以换入一个不碰磁盘的转写函数。"""

    def __init__(self, settings: Settings, engine: Callable[[bytes, str], str] | None = None) -> None:
        self.settings = settings
        self.engine = engine

    def available(self) -> bool:
        """本机已有转写引擎时才显示录音控件。"""
        if self.engine is not None:
            return True
        return importlib.util.find_spec("faster_whisper") is not None

    def transcribe(self, audio: bytes, suffix: str) -> str:
        """转写一段录音。过程不把文字或音频写进日志。"""
        if not audio:
            raise ValueError("没有录音")
        if len(audio) > 8 * 1024 * 1024:
            raise ValueError("录音超过大小限制")
        cleaned = _suffix(suffix)
        if not self.available():
            raise ValueError("本机语音还没有安装")
        try:
            if self.engine is not None:
                text = self.engine(audio, cleaned)
            else:
                text = _whisper(self.settings, audio, cleaned)
        except ValueError:
            raise
        except Exception:
            safe_log("voice_failed", ok=0, reason="transcribe")
            raise ValueError("本机转写没有完成") from None
        result = (text or "").strip()
        if len(result) > 2000:
            result = result[:2000]
        return result


def _suffix(value: str) -> str:
    text = value.strip().lower()
    if not text.startswith("."):
        text = "." + text
    if text not in _SUFFIXES:
        raise ValueError("不支持的录音格式")
    return text


def _whisper(settings: Settings, audio: bytes, suffix: str) -> str:
    """使用已安装的 faster-whisper。模型文件放在本机数据目录。"""
    from faster_whisper import WhisperModel

    folder = settings.data_dir / "tmp"
    folder.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(delete=False, dir=folder, suffix=suffix)
    path = Path(handle.name)
    try:
        handle.write(audio)
        handle.close()
        if _MODEL["obj"] is None:
            _MODEL["obj"] = WhisperModel(
                "small",
                device="cpu",
                compute_type="int8",
                download_root=str(settings.data_dir / "models"),
            )
        model = _MODEL["obj"]
        segments, _info = model.transcribe(str(path), language="zh")
        return "".join(segment.text for segment in segments)
    finally:
        path.unlink(missing_ok=True)
