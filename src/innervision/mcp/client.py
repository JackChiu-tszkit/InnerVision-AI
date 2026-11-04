"""进程内 MCP 客户端。界面和 Agent 用它调用本机工具。"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from fastmcp import Client


class SyncMCPClient:
    """把异步 MCP 客户端包成同步调用，便于 Streamlit 和测试使用。"""

    def __init__(self, server: Any) -> None:
        self.server = server

    def call(self, name: str, arguments: dict | None = None) -> Any:
        """调用一个工具并解开返回值。参数不会写入日志。"""
        return asyncio.run(self._call(name, arguments or {}))

    def list_tool_names(self) -> list[str]:
        """列出服务器注册的工具名称。"""
        return asyncio.run(self._names())

    async def _call(self, name: str, arguments: dict) -> Any:
        async with Client(self.server) as client:
            result = await client.call_tool(name, arguments)
        return unwrap_tool_result(result)

    async def _names(self) -> list[str]:
        async with Client(self.server) as client:
            tools = await client.list_tools()
        names = []
        for tool in tools:
            if isinstance(tool, dict):
                names.append(str(tool["name"]))
            else:
                names.append(str(tool.name))
        return names


def unwrap_tool_result(result: Any) -> Any:
    """兼容 FastMCP 不同版本的工具返回结构。"""
    data = getattr(result, "data", None)
    if data is not None:
        return data
    structured = getattr(result, "structured_content", None)
    if isinstance(structured, dict):
        if set(structured) == {"result"}:
            return structured["result"]
        return structured
    content = getattr(result, "content", None) or []
    if content:
        text = getattr(content[0], "text", None)
        if isinstance(text, str):
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                return text
    if isinstance(result, str):
        try:
            return json.loads(result)
        except json.JSONDecodeError:
            return result
    return result
