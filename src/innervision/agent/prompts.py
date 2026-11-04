"""系统提示与本地模型探测。"""

from __future__ import annotations

import json
import time
import urllib.request
from pathlib import Path

from innervision.config import Settings, assert_loopback_url
from innervision.logging_setup import safe_log

SKILL_FILES = {
    "gratitude": "gratitude-journal/SKILL.md",
    "vision": "vision-board/SKILL.md",
    "sedona": "sedona-release/SKILL.md",
    "action": "action-coach/SKILL.md",
    "ritual": "daily-ritual/SKILL.md",
    "report": "weekly-report/SKILL.md",
}

_PROBE = {"url": "", "at": 0.0, "ok": False}


def load_skill(skills_dir: Path, intent: str) -> str:
    """只读取内置技能文件，不执行其中的任何内容。"""
    relative = SKILL_FILES.get(intent)
    if relative is None:
        return ""
    root = skills_dir.resolve()
    path = (root / relative).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise ValueError("技能文件不可用")
    return path.read_text(encoding="utf-8")


def build_system_prompt(skill_text: str) -> str:
    """约束本地模型：只读工具、不诊断、不承诺结果、不强迫释放。"""
    return (
        "你是 InnerVision AI 的本地助手，只整理用户自己的记录。\n"
        "你只能使用提供的只读工具。不要声称已经保存或已经写入。\n"
        "不要判断用户的心理状况，不要给出诊断。\n"
        "不要承诺显化、疗愈或任何具体结果。\n"
        "不要要求用户必须放下或必须释放情绪。\n"
        "如果需要发问，一次只问一个问题。\n\n"
        + skill_text
    )


def probe_ollama(base_url: str) -> bool:
    """短超时检查本机 Ollama 是否在线。结果会缓存三十秒。"""
    url = assert_loopback_url(base_url)
    now = time.monotonic()
    if _PROBE["url"] == url and now - float(_PROBE["at"]) < 30:
        return bool(_PROBE["ok"])
    ok = False
    try:
        request = urllib.request.Request(url + "/api/tags", method="GET")
        with urllib.request.urlopen(request, timeout=0.4) as response:
            ok = response.status == 200
    except Exception:
        ok = False
    _PROBE.update(url=url, at=now, ok=ok)
    return ok


def reset_probe_cache() -> None:
    """测试时清掉模型探测缓存。"""
    _PROBE.update(url="", at=0.0, ok=False)


class OllamaQuestionPhraser:
    """用本地模型改写释放练习的固定问句。失败或越界时退回模板。"""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def phrase(self, template: str, step: str) -> str:
        """改写问句。不把用户的感受正文发给模型。"""
        if not self.settings.enable_llm or not probe_ollama(self.settings.ollama_base_url):
            return template
        try:
            from langchain_core.messages import HumanMessage, SystemMessage
            from langchain_ollama import ChatOllama

            model = ChatOllama(
                model=self.settings.ollama_model,
                base_url=self.settings.ollama_base_url,
                temperature=0.2,
                num_predict=120,
                timeout=6,
            )
            response = model.invoke(
                [
                    SystemMessage(
                        content=(
                            "只把模板改写得更温和。必须只保留一个问号。"
                            "不要诊断，不要承诺结果，不要要求用户必须放下。"
                            "做不到时原样返回模板。"
                        )
                    ),
                    HumanMessage(content=f"步骤：{step}\n模板：{template}"),
                ]
            )
            content = response.content
            return content.strip() if isinstance(content, str) else template
        except Exception:
            safe_log("phrase_fallback", state=step, reason="llm_error", ok=0)
            return template


def dumps(payload: object) -> str:
    """把工具结果转成模型可读的 JSON。"""
    return json.dumps(payload, ensure_ascii=False)
