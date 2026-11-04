"""从自然语言里提取结构化草稿。提取失败时不保存。"""

from __future__ import annotations

import re


def extract_gratitude_items(text: str) -> list[str] | None:
    """从分行或分号中提取三件感恩事项。"""
    lines = []
    for raw in text.splitlines():
        line = raw.strip()
        line = re.sub(
            r"^(请)?(帮我)?(记录|保存)?(一下)?(今天的)?(感恩|感谢)(日记)?[:：]?\s*",
            "",
            line,
        )
        line = re.sub(r"^(\d+[\.、\)]\s*|[-*]\s*)", "", line).strip()
        if line:
            lines.append(line)
    if len(lines) >= 3:
        return lines[:3]
    parts = [part.strip() for part in re.split(r"[；;]", text) if part.strip()]
    cleaned = []
    for part in parts:
        item = re.sub(r"^(\d+[\.、\)]\s*)", "", part).strip()
        item = re.sub(r"^(帮我)?(记录)?(感恩|感谢)[:：]\s*", "", item).strip()
        if item:
            cleaned.append(item)
    if len(cleaned) >= 3:
        return cleaned[:3]
    return None


def extract_vision(text: str) -> dict | None:
    """提取愿景标题、分类和可选日期。"""
    match = re.search(r"(?:愿景|目标)[:：]\s*([^\n]+)", text)
    if match is None:
        return None
    title = re.split(r"\s+分类[:：]", match.group(1).strip())[0].strip()
    if not title:
        return None
    category_match = re.search(r"分类[:：]\s*(\S+)", text)
    date_match = re.search(r"(20\d{2}-\d{2}-\d{2})", text)
    return {
        "title": title[:80],
        "description": "",
        "category": category_match.group(1)[:20] if category_match else "其他",
        "target_date": date_match.group(1) if date_match else None,
        "milestones": [],
        "image_base64": "",
        "image_ext": "",
    }


def extract_action(text: str) -> dict | None:
    """提取行动标题和 P1/P2。缺少优先级时返回空，让界面先追问一次。"""
    if re.search(r"P1|紧急且重要|紧急", text, re.IGNORECASE):
        priority = "P1"
    elif re.search(r"P2|积累|以后", text, re.IGNORECASE):
        priority = "P2"
    else:
        return None
    match = re.search(r"(?:任务|行动)[:：]\s*([^\n]+)", text)
    if match is None:
        return None
    title = match.group(1).strip()
    if not title:
        return None
    return {"title": title[:120], "detail": "", "priority": priority, "vision_id": None}


def find_task_to_complete(text: str, tasks: list[dict]) -> int | None:
    """根据编号或标题找到要完成的未完成行动。"""
    if re.search(r"完成|做完", text) is None:
        return None
    id_match = re.search(r"(?:任务|行动)\s*#?(\d+)", text)
    if id_match:
        task_id = int(id_match.group(1))
        if any(task["id"] == task_id and task["status"] == "open" for task in tasks):
            return task_id
    for task in tasks:
        title = task.get("title") or ""
        if task.get("status") == "open" and title and title in text:
            return int(task["id"])
    return None
