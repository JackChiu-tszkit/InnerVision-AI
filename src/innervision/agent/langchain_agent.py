"""只读的 LangChain 工具循环。写入工具不会交给模型。"""

from __future__ import annotations

from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import StructuredTool

from innervision.config import Settings
from innervision.logging_setup import safe_log
from innervision.mcp.client import SyncMCPClient
from innervision.mcp.server import LLM_VISIBLE_TOOLS
from innervision.timeutil import SystemClock, monday_of


class ReadOnlyAgent:
    """让本地模型通过 MCP 只读工具查看记录，并在三步内结束。"""

    def __init__(
        self,
        tools: list[StructuredTool],
        settings: Settings,
        llm: Any | None = None,
    ) -> None:
        self.tools = tools
        self.settings = settings
        self.llm = llm

    def run(self, system: str, user: str) -> str:
        """执行最多三轮工具调用。模型不持有写入工具。"""
        llm = self.llm or self._build_llm()
        bound = llm.bind_tools(self.tools)
        messages: list[Any] = [SystemMessage(content=system), HumanMessage(content=user)]
        tool_map = {tool.name: tool for tool in self.tools}
        for _ in range(3):
            ai_message = bound.invoke(messages)
            messages.append(ai_message)
            calls = getattr(ai_message, "tool_calls", None) or []
            if not calls:
                content = getattr(ai_message, "content", "")
                return content if isinstance(content, str) else str(content)
            for call in calls:
                name, args, call_id = _call_parts(call)
                if name not in tool_map or name not in LLM_VISIBLE_TOOLS:
                    output = '{"ok": false, "message": "工具不可用"}'
                else:
                    output = _invoke_tool(tool_map[name], args, name)
                messages.append(ToolMessage(content=output, tool_call_id=call_id))
        return ""

    def _build_llm(self) -> Any:
        from langchain_ollama import ChatOllama

        return ChatOllama(
            model=self.settings.ollama_model,
            base_url=self.settings.ollama_base_url,
            temperature=0.2,
            num_predict=400,
            timeout=8,
        )


def build_read_tools(client: SyncMCPClient, clock: SystemClock) -> list[StructuredTool]:
    """把 MCP 只读工具包装成 LangChain 工具。"""

    def list_gratitude_entries(limit: int = 20) -> str:
        """读取最近的感恩日记。"""
        return _call(client, "list_gratitude_entries", {"limit": limit})

    def gratitude_weekly_review(week_start: str = "") -> str:
        """读取感恩周回顾。留空则使用本周。"""
        start = week_start.strip() or monday_of(clock.today()).isoformat()
        return _call(client, "gratitude_weekly_review", {"week_start": start})

    def list_vision_cards() -> str:
        """读取愿景卡片。"""
        return _call(client, "list_vision_cards", {})

    def list_action_tasks(status: str = "", priority: str = "") -> str:
        """读取行动任务。"""
        return _call(client, "list_action_tasks", {"status": status, "priority": priority})

    def action_weekly_review(week_start: str = "") -> str:
        """读取行动周回顾。留空则使用本周。"""
        start = week_start.strip() or monday_of(clock.today()).isoformat()
        return _call(client, "action_weekly_review", {"week_start": start})

    def sedona_session_summary(limit: int = 10) -> str:
        """读取释放练习摘要，不含感受正文。"""
        return _call(client, "sedona_session_summary", {"limit": limit})

    functions = [
        list_gratitude_entries,
        gratitude_weekly_review,
        list_vision_cards,
        list_action_tasks,
        action_weekly_review,
        sedona_session_summary,
    ]
    return [StructuredTool.from_function(func) for func in functions]


def _call(client: SyncMCPClient, name: str, arguments: dict) -> str:
    from innervision.agent.prompts import dumps

    try:
        payload = client.call(name, arguments)
    except Exception:
        safe_log("tool_error", tool=name, reason="exception", ok=0)
        payload = {"ok": False, "message": "读取失败"}
    return dumps(payload)


def _invoke_tool(tool: StructuredTool, args: dict, name: str) -> str:
    try:
        output = tool.invoke(args)
    except Exception:
        safe_log("tool_error", tool=name, reason="exception", ok=0)
        return '{"ok": false, "message": "读取失败"}'
    return output if isinstance(output, str) else str(output)


def _call_parts(call: Any) -> tuple[str, dict, str]:
    if isinstance(call, dict):
        return str(call.get("name")), dict(call.get("args") or {}), str(call.get("id") or "call")
    name = str(getattr(call, "name", ""))
    args = dict(getattr(call, "args", {}) or {})
    call_id = str(getattr(call, "id", "") or "call")
    return name, args, call_id
