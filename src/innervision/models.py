"""写入数据的形状校验。校验失败时不回显用户原文。"""

from __future__ import annotations

import re
from datetime import date
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from innervision.safety import is_safe_report


def _clean_text(value: str, *, limit: int, empty_ok: bool = False) -> str:
    text = value.strip()
    if not text and not empty_ok:
        raise ValueError("内容不能为空")
    if len(text) > limit:
        raise ValueError("内容超过长度限制")
    return text


class GratitudeCreate(BaseModel):
    """一天三条感恩事项。同一天再次保存会覆盖当天记录。"""

    entry_date: str
    item_1: str
    item_2: str
    item_3: str
    reflection: str = ""

    @field_validator("entry_date")
    @classmethod
    def _date(cls, value: str) -> str:
        date.fromisoformat(value)
        return value

    @field_validator("item_1", "item_2", "item_3")
    @classmethod
    def _item(cls, value: str) -> str:
        return _clean_text(value, limit=500)

    @field_validator("reflection")
    @classmethod
    def _reflection(cls, value: str) -> str:
        return _clean_text(value, limit=2000, empty_ok=True)


class GratitudeDelete(BaseModel):
    entry_id: int = Field(gt=0)


class VisionCreate(BaseModel):
    """愿景卡片。图片以本机文件名关联，不接收任意路径。"""

    title: str
    description: str = ""
    category: str
    target_date: str | None = None
    milestones: list[str] = Field(default_factory=list)
    image_base64: str = ""
    image_ext: str = ""

    @field_validator("title")
    @classmethod
    def _title(cls, value: str) -> str:
        return _clean_text(value, limit=80)

    @field_validator("description")
    @classmethod
    def _description(cls, value: str) -> str:
        return _clean_text(value, limit=2000, empty_ok=True)

    @field_validator("category")
    @classmethod
    def _category(cls, value: str) -> str:
        text = _clean_text(value, limit=20)
        if any(char in text for char in "\n\r/\\"):
            raise ValueError("分类无效")
        return text

    @field_validator("target_date")
    @classmethod
    def _target(cls, value: str | None) -> str | None:
        if value is None or value == "":
            return None
        date.fromisoformat(value)
        return value

    @field_validator("milestones")
    @classmethod
    def _milestones(cls, value: list[str]) -> list[str]:
        items = [_clean_text(item, limit=120) for item in value if item.strip()]
        if len(items) > 20:
            raise ValueError("里程碑过多")
        return items


class VisionDelete(BaseModel):
    vision_id: int = Field(gt=0)


class BoardLayout(BaseModel):
    """愿景板从左到右、从上到下的卡片顺序。"""

    ordered_ids: list[int] = Field(min_length=1, max_length=200)

    @field_validator("ordered_ids")
    @classmethod
    def _ids(cls, value: list[int]) -> list[int]:
        if any(item <= 0 for item in value):
            raise ValueError("卡片编号无效")
        if len(set(value)) != len(value):
            raise ValueError("卡片顺序有重复")
        return value


class MilestoneToggle(BaseModel):
    milestone_id: int = Field(gt=0)
    done: bool


class ImageAttach(BaseModel):
    vision_id: int = Field(gt=0)
    image_base64: str
    image_ext: str


class ActionCreate(BaseModel):
    """P1 是紧急且重要，P2 是为以后积累。"""

    title: str
    detail: str = ""
    priority: Literal["P1", "P2"]
    vision_id: int | None = None

    @field_validator("title")
    @classmethod
    def _title(cls, value: str) -> str:
        return _clean_text(value, limit=120)

    @field_validator("detail")
    @classmethod
    def _detail(cls, value: str) -> str:
        return _clean_text(value, limit=2000, empty_ok=True)


class ActionIdentity(BaseModel):
    task_id: int = Field(gt=0)


class ActionLink(BaseModel):
    task_id: int = Field(gt=0)
    vision_id: int = Field(gt=0)


class WeeklyNote(BaseModel):
    week_start: str
    notes: str = ""

    @field_validator("week_start")
    @classmethod
    def _week(cls, value: str) -> str:
        found = date.fromisoformat(value)
        if found.weekday() != 0:
            raise ValueError("周回顾必须从周一开始")
        return value

    @field_validator("notes")
    @classmethod
    def _notes(cls, value: str) -> str:
        return _clean_text(value, limit=2000, empty_ok=True)


class SedonaStart(BaseModel):
    kind: Literal["start"] = "start"


class SedonaStep(BaseModel):
    session_id: int = Field(gt=0)
    user_text: str

    @field_validator("user_text")
    @classmethod
    def _text(cls, value: str) -> str:
        return _clean_text(value, limit=2000)


class SedonaResume(BaseModel):
    session_id: int = Field(gt=0)


class RitualStart(BaseModel):
    kind: Literal["morning", "evening"]


class RitualStep(BaseModel):
    session_id: int = Field(gt=0)
    user_text: str

    @field_validator("user_text")
    @classmethod
    def _text(cls, value: str) -> str:
        return _clean_text(value, limit=2000)


class RitualResume(BaseModel):
    session_id: int = Field(gt=0)


class GrowthReportSave(BaseModel):
    """每周成长报告。正文必须通过安全陈述检查。"""

    week_start: str
    body: str
    source: Literal["template", "local_model"]

    @field_validator("week_start")
    @classmethod
    def _week(cls, value: str) -> str:
        found = date.fromisoformat(value)
        if found.weekday() != 0:
            raise ValueError("周报告必须从周一开始")
        return value

    @field_validator("body")
    @classmethod
    def _body(cls, value: str) -> str:
        text = _clean_text(value, limit=800)
        if not is_safe_report(text):
            raise ValueError("报告内容不符合边界")
        return text


_BACKUP_NAME = re.compile(r"^innervision-\d{8}-\d{6}\.zip$")


class RestoreBackup(BaseModel):
    """恢复备份会覆盖当前记录，因此必须带上固定确认句和文件名。"""

    backup_name: str
    confirm_text: str

    @field_validator("backup_name")
    @classmethod
    def _name(cls, value: str) -> str:
        if _BACKUP_NAME.fullmatch(value.strip()) is None:
            raise ValueError("备份文件名无效")
        return value.strip()

    @field_validator("confirm_text")
    @classmethod
    def _phrase(cls, value: str) -> str:
        if value.strip() != "恢复本地备份":
            raise ValueError("确认语句不匹配")
        return "恢复本地备份"


class DeleteAllData(BaseModel):
    """清空数据必须带上固定确认句，避免误触。"""

    confirm_text: str

    @field_validator("confirm_text")
    @classmethod
    def _phrase(cls, value: str) -> str:
        if value.strip() != "删除全部本地数据":
            raise ValueError("确认语句不匹配")
        return "删除全部本地数据"
