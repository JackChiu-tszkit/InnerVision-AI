"""FastMCP 工具。模型只能看到只读工具，写入必须先确认再核销。"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastmcp import FastMCP
from pydantic import ValidationError

from innervision.auth import AuthorizationError
from innervision.logging_setup import safe_log
from innervision.services.actions import ActionService
from innervision.services.dispatcher import Dispatcher
from innervision.services.gratitude import GratitudeService
from innervision.services.sedona import SedonaService
from innervision.services.vision import VisionService

READ_TOOLS = {
    "list_gratitude_entries",
    "gratitude_weekly_review",
    "list_vision_cards",
    "list_action_tasks",
    "action_weekly_review",
    "sedona_session_summary",
}
PROPOSE_TOOLS = {
    "propose_gratitude_entry",
    "propose_vision_card",
    "propose_action_task",
    "propose_complete_action",
    "propose_sedona_start",
    "propose_sedona_step",
}
COMMIT_TOOLS = {"commit_write"}
LLM_VISIBLE_TOOLS = frozenset(READ_TOOLS)


def build_server(
    gratitude: GratitudeService,
    vision: VisionService,
    actions: ActionService,
    sedona: SedonaService,
    dispatcher: Dispatcher,
) -> FastMCP:
    """创建只访问本机业务服务的 MCP 服务器。"""
    mcp = FastMCP("InnerVision AI")

    @mcp.tool
    def list_gratitude_entries(limit: int = 20) -> dict:
        """读取最近的感恩日记。"""
        return _guard(lambda: {"ok": True, "entries": gratitude.list_recent(limit)})

    @mcp.tool
    def gratitude_weekly_review(week_start: str) -> dict:
        """读取某一周的感恩回顾。week_start 可以是这一周里的任意一天。"""
        return _guard(lambda: gratitude.weekly_review(week_start))

    @mcp.tool
    def list_vision_cards() -> dict:
        """读取愿景卡片和里程碑。"""
        return _guard(lambda: {"ok": True, "cards": vision.list_cards()})

    @mcp.tool
    def list_action_tasks(status: str = "", priority: str = "") -> dict:
        """读取行动任务。status 可为空、open 或 done；priority 可为空、P1 或 P2。"""
        return _guard(
            lambda: {
                "ok": True,
                "tasks": actions.list_tasks(status or None, priority or None),
            }
        )

    @mcp.tool
    def action_weekly_review(week_start: str) -> dict:
        """读取某一周的行动回顾。"""
        return _guard(lambda: actions.weekly_review(week_start))

    @mcp.tool
    def sedona_session_summary(limit: int = 10) -> dict:
        """读取释放练习摘要。不返回感受正文。"""
        return _guard(lambda: {"ok": True, "sessions": sedona.list_summaries(limit)})

    @mcp.tool
    def propose_gratitude_entry(
        entry_date: str,
        item_1: str,
        item_2: str,
        item_3: str,
        reflection: str = "",
    ) -> dict:
        """提出一条感恩日记。确认之前不会保存。"""
        payload = {
            "entry_date": entry_date,
            "item_1": item_1,
            "item_2": item_2,
            "item_3": item_3,
            "reflection": reflection,
        }
        return _propose(gratitude.propose_create, payload, "gratitude.create")

    @mcp.tool
    def propose_vision_card(
        title: str,
        category: str,
        description: str = "",
        target_date: str = "",
        milestones: str = "",
    ) -> dict:
        """提出一张愿景卡片。milestones 用换行分隔。确认之前不会保存。"""
        payload = {
            "title": title,
            "category": category,
            "description": description,
            "target_date": target_date or None,
            "milestones": [line.strip() for line in milestones.splitlines() if line.strip()],
            "image_base64": "",
            "image_ext": "",
        }
        return _propose(vision.propose_create, payload, "vision.create")

    @mcp.tool
    def propose_action_task(
        title: str,
        priority: str,
        detail: str = "",
        vision_id: int = 0,
    ) -> dict:
        """提出一项 P1 或 P2 行动。确认之前不会保存。"""
        payload = {
            "title": title,
            "priority": priority,
            "detail": detail,
            "vision_id": vision_id or None,
        }
        return _propose(actions.propose_create, payload, "action.create")

    @mcp.tool
    def propose_complete_action(task_id: int) -> dict:
        """提出把一项行动标为完成。确认之前不会改变状态。"""
        return _propose(lambda _payload: actions.propose_complete(task_id), {}, "action.complete")

    @mcp.tool
    def propose_sedona_start() -> dict:
        """提出开始一次释放练习。确认之前不会创建练习。"""
        return _guard(
            lambda: _token_result(sedona.propose_start(), "sedona.start", {"步骤": "开始练习"})
        )

    @mcp.tool
    def propose_sedona_step(session_id: int, user_text: str) -> dict:
        """提出保存练习中的一步。确认之前不会保存。"""
        return _guard(
            lambda: _token_result(
                sedona.propose_step(session_id, user_text),
                "sedona.step",
                {"练习": session_id, "这一步": user_text},
            )
        )

    @mcp.tool
    def commit_write(token: str) -> dict:
        """核销一条已经由用户确认的写入。未确认时会拒绝。"""
        return _guard(lambda: dispatcher.commit(token))

    return mcp


def main() -> None:
    """在本机回环地址启动 MCP。导入本模块时不会监听端口。"""
    from innervision.config import load_settings
    from innervision.container import build_container
    from innervision.logging_setup import configure_logging

    settings = load_settings()
    configure_logging(settings.log_dir)
    container = build_container(settings)
    container.mcp_server.run(transport="http", host=settings.mcp_host, port=settings.mcp_port)


def _propose(factory: Callable[[dict], str], payload: dict, action: str) -> dict:
    def run() -> dict:
        token = factory(payload)
        return _token_result(token, action, _preview(payload))

    return _guard(run)


def _token_result(token: str, action: str, preview: dict) -> dict:
    safe_log("mcp_tool", tool=action, ok=1)
    return {"ok": True, "token": token, "action": action, "preview": preview}


def _preview(payload: dict) -> dict:
    hidden = {"image_base64", "image_ext"}
    preview = {}
    for key, value in payload.items():
        if key in hidden or value in ("", None, []):
            continue
        preview[key] = value
    return preview


def _guard(func: Callable[[], Any]) -> dict:
    """把失败收成简短说明，避免校验异常把正文带回调用方。"""
    try:
        result = func()
    except AuthorizationError as exc:
        safe_log("mcp_tool", ok=0, reason="authorization")
        return {"ok": False, "message": str(exc)}
    except ValidationError:
        safe_log("mcp_tool", ok=0, reason="validation")
        return {"ok": False, "message": "内容未通过校验，请检查必填项。"}
    except ValueError as exc:
        message = str(exc)
        if len(message) > 80:
            message = "操作无法完成"
        safe_log("mcp_tool", ok=0, reason="value")
        return {"ok": False, "message": message}
    except Exception:
        safe_log("mcp_tool", ok=0, reason="error")
        return {"ok": False, "message": "操作无法完成"}
    if isinstance(result, dict):
        return result
    return {"ok": True, "result": result}
