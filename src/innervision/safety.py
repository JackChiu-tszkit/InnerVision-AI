"""安全边界：识别即时风险，并限制引导语一次只问一个问题。"""

from __future__ import annotations

import re

# 只用较明确的表达，避免把“结束练习”或“结束这段关系”误判为危机。
_CRISIS_PATTERNS = (
    r"自杀",
    r"不想活",
    r"活不下去",
    r"结束生命",
    r"结束自己的生命",
    r"结束自己",
    r"想死",
    r"去死",
    r"杀了我自己",
    r"自残",
    r"割腕",
    r"suicide",
    r"kill myself",
    r"end my life",
    r"self-harm",
    r"self harm",
    r"want to die",
)
_CRISIS_RE = re.compile("|".join(_CRISIS_PATTERNS), re.IGNORECASE)

_FORBIDDEN_GUIDANCE = (
    "诊断",
    "抑郁症",
    "焦虑症",
    "双相",
    "强迫症",
    "一定会实现",
    "保证显化",
    "保证你会",
    "必须放下",
    "你应该释放",
    "你必须释放",
    "疗愈你",
)

_QUESTION_RE = re.compile(r"[?？]")


def is_crisis_text(text: str) -> bool:
    """判断这句话是否包含即时安全风险。普通难过不会触发。"""
    return _CRISIS_RE.search(text or "") is not None


def support_message() -> str:
    """停止普通练习后给出的现实支持说明。这句话不由模型改写。"""
    return (
        "我注意到你提到了可能危及安全的内容，这次练习先停在这里。"
        "我不能判断你的心理状况，也不是危机干预服务。"
        "如果你正处在即时危险中，请立刻联系当地紧急服务：在欧洲可拨打 112，在中国可拨打 110 或 120。"
        "你也可以联系当地心理危机热线。在中国可拨打 12356，"
        "北京心理危机研究与干预中心的电话是 010-82951332。"
        "如果可以，请联系一位你信任的人，让对方陪在身边。"
    )


def question_count(text: str) -> int:
    """统计问句数量。释放练习每一步只允许一个核心问题。"""
    return len(_QUESTION_RE.findall(text or ""))


def is_safe_single_question(text: str) -> bool:
    """模型改写结果必须仍是一个问题，且不包含诊断、强迫或结果承诺。"""
    cleaned = (text or "").strip()
    if not cleaned or len(cleaned) > 400:
        return False
    if question_count(cleaned) != 1:
        return False
    return not any(phrase in cleaned for phrase in _FORBIDDEN_GUIDANCE)


def is_safe_statement(text: str) -> bool:
    """结束语和支持说明不应追问，也不应承诺结果。"""
    cleaned = (text or "").strip()
    if not cleaned or question_count(cleaned) != 0:
        return False
    return not any(phrase in cleaned for phrase in _FORBIDDEN_GUIDANCE)


_REPORT_BANNED = ("一定会", "一定能", "一定可以", "保证", "显化", "疗愈", "诊断", "必须放下")


def is_safe_report(text: str) -> bool:
    """每周报告只能是陈述，不能诊断、保证或追问。"""
    cleaned = (text or "").strip()
    if not cleaned or len(cleaned) > 800:
        return False
    if not is_safe_statement(cleaned):
        return False
    return not any(phrase in cleaned for phrase in _REPORT_BANNED)
