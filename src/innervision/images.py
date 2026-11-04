"""愿景图片只写入数据目录，并做基本的类型检查。"""

from __future__ import annotations

import base64
import re
import uuid
from pathlib import Path

MAX_IMAGE_BYTES = 8 * 1024 * 1024
ALLOWED_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
_NAME_RE = re.compile(r"^[a-f0-9]{32}\.(png|jpg|jpeg|webp|gif)$")


def normalize_ext(ext: str) -> str:
    """把扩展名整理成小写并带上点。"""
    text = ext.strip().lower()
    if not text:
        raise ValueError("不支持的图片类型")
    if not text.startswith("."):
        text = "." + text
    if text not in ALLOWED_EXTENSIONS:
        raise ValueError("不支持的图片类型")
    return text


def decode_image(image_base64: str, ext: str) -> tuple[bytes, str]:
    """解码上传内容。扩展名和文件头不一致时拒绝，避免把任意文件存进来。"""
    suffix = normalize_ext(ext)
    if len(image_base64) > 12_000_000:
        raise ValueError("图片超过大小限制")
    try:
        data = base64.b64decode(image_base64, validate=True)
    except Exception as exc:
        raise ValueError("图片无法读取") from exc
    if not data or len(data) > MAX_IMAGE_BYTES:
        raise ValueError("图片为空或超过 8MB")
    if not _signature_matches(data, suffix):
        raise ValueError("图片内容与类型不符")
    return data, suffix


def save_image(image_dir: Path, data: bytes, ext: str) -> str:
    """用随机文件名保存图片，返回文件名而不是用户提供的路径。"""
    suffix = normalize_ext(ext)
    image_dir.mkdir(parents=True, exist_ok=True)
    name = f"{uuid.uuid4().hex}{suffix}"
    target = resolve_image(image_dir, name)
    target.write_bytes(data)
    return name


def resolve_image(image_dir: Path, name: str) -> Path:
    """只解析数据目录里的随机文件名，拒绝路径穿越。"""
    if not name or not _NAME_RE.fullmatch(name):
        raise ValueError("非法图片名")
    root = image_dir.resolve()
    target = (root / name).resolve()
    if target.parent != root:
        raise ValueError("非法图片路径")
    return target


def image_payload(filename: str, data: bytes) -> dict[str, str]:
    """把上传文件转成可校验的图片载荷。调用前不会把原文件名写入磁盘。"""
    encoded = base64.b64encode(data).decode("ascii")
    _raw, suffix = decode_image(encoded, Path(filename).suffix)
    return {"image_base64": encoded, "image_ext": suffix}


def remove_image(image_dir: Path, name: str | None) -> None:
    """删除一张已保存的图片。名字不合法时直接忽略。"""
    if not name:
        return
    try:
        resolve_image(image_dir, name).unlink(missing_ok=True)
    except ValueError:
        return


def _signature_matches(data: bytes, ext: str) -> bool:
    if ext == ".png":
        return data.startswith(b"\x89PNG\r\n\x1a\n")
    if ext in {".jpg", ".jpeg"}:
        return data.startswith(b"\xff\xd8\xff")
    if ext == ".gif":
        return data.startswith((b"GIF87a", b"GIF89a"))
    if ext == ".webp":
        return len(data) >= 12 and data.startswith(b"RIFF") and data[8:12] == b"WEBP"
    return False
