"""意图识别。开放中的释放练习只在没有更明确意图时接管对话。"""

from __future__ import annotations

import re
from dataclasses import dataclass

PATTERNS = (
    ("gratitude", re.compile(r"感恩|感谢|值得庆幸|gratitude", re.IGNORECASE)),
    ("vision", re.compile(r"愿景|愿景板|目标卡|vision", re.IGNORECASE)),
    ("sedona", re.compile(r"释放|放下练习|sedona|感觉练习", re.IGNORECASE)),
    ("action", re.compile(r"行动|任务|待办|\bP1\b|\bP2\b|紧急且重要", re.IGNORECASE)),
)
READ_RE = re.compile(r"看看|查看|列出|回顾|有哪些|多少|本周|总结")
WRITE_RE = re.compile(r"记录|保存|新增|创建|添加|完成|删除|做成")
READ_PRIORITY_RE = re.compile(r"回顾|看看|查看|列出|总结")


@dataclass(frozen=True)
class Decision:
    """对话要进入的功能和读写方式。"""

    intent: str
    mode: str


def classify(text: str, *, sedona_open: bool = False) -> Decision:
    """根据关键词判断用户想做感恩、愿景、释放练习还是行动。"""
    positions = []
    for name, pattern in PATTERNS:
        match = pattern.search(text)
        if match:
            positions.append((match.start(), name))
    if not positions:
        if sedona_open:
            return Decision("sedona", "practice")
        return Decision("unknown", "chat")
    positions.sort()
    return Decision(positions[0][1], detect_mode(positions[0][1], text))


def detect_mode(intent: str, text: str) -> str:
    """区分查看、写入和释放练习。释放练习始终走状态流程。"""
    if intent == "sedona":
        # 明确是在查看时才走摘要；开始或继续练习仍进入状态流程。
        if READ_RE.search(text) and re.search(r"开始|做|进行|进入", text) is None:
            return "read"
        return "practice"
    if intent == "unknown":
        return "chat"
    is_read = READ_RE.search(text) is not None
    is_write = WRITE_RE.search(text) is not None
    if is_read and not is_write:
        return "read"
    if is_write and not is_read:
        return "write"
    if is_read and is_write:
        return "read" if READ_PRIORITY_RE.search(text) else "write"
    return "chat"
