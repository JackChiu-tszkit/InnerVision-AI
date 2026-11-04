"""组装数据库、服务、MCP 和本地助手。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from innervision.agent.langchain_agent import ReadOnlyAgent, build_read_tools
from innervision.agent.orchestrator import Orchestrator
from innervision.agent.prompts import OllamaQuestionPhraser, probe_ollama
from innervision.auth import AuthorizationService
from innervision.config import Settings
from innervision.db import Database
from innervision.logging_setup import configure_logging
from innervision.mcp.client import SyncMCPClient
from innervision.mcp.server import build_server
from innervision.services.actions import ActionService
from innervision.services.backup import BackupService
from innervision.services.dispatcher import Dispatcher
from innervision.services.gratitude import GratitudeService
from innervision.services.privacy import PrivacyService
from innervision.services.report import ReportService
from innervision.services.ritual import RitualService
from innervision.services.sedona import SedonaService
from innervision.services.stats import StatsService
from innervision.services.vision import VisionService
from innervision.services.voice import VoiceService
from innervision.timeutil import SystemClock


@dataclass
class Container:
    """一次运行所需的本地对象。"""

    settings: Settings
    db: Database
    auth: AuthorizationService
    gratitude: GratitudeService
    vision: VisionService
    actions: ActionService
    sedona: SedonaService
    privacy: PrivacyService
    stats: StatsService
    reports: ReportService
    rituals: RitualService
    backup: BackupService
    voice: VoiceService
    dispatcher: Dispatcher
    mcp_server: Any
    mcp: SyncMCPClient
    read_tools: list
    read_agent: ReadOnlyAgent
    orchestrator: Orchestrator


def build_container(settings: Settings) -> Container:
    """按依赖顺序创建本地服务。监听地址来自配置，固定为本机。"""
    configure_logging(settings.log_dir)
    settings.image_dir.mkdir(parents=True, exist_ok=True)
    settings.export_dir.mkdir(parents=True, exist_ok=True)
    settings.backup_dir.mkdir(parents=True, exist_ok=True)
    database = Database(settings.db_path)
    auth = AuthorizationService(database)
    gratitude = GratitudeService(database, auth)
    vision = VisionService(database, auth, settings.image_dir)
    actions = ActionService(database, auth)
    sedona = SedonaService(database, auth, OllamaQuestionPhraser(settings))
    privacy = PrivacyService(database, auth, settings.image_dir, settings.log_dir, settings.backup_dir)
    stats = StatsService(database)
    reports = ReportService(database, auth, settings)
    rituals = RitualService(database, auth)
    backup = BackupService(database, auth, settings.image_dir, settings.backup_dir)
    voice = VoiceService(settings)
    dispatcher = Dispatcher(
        auth,
        {
            "gratitude.create": gratitude.save_authorized,
            "gratitude.delete": gratitude.delete_authorized,
            "vision.create": vision.save_authorized,
            "vision.delete": vision.delete_authorized,
            "vision.milestone": vision.toggle_milestone_authorized,
            "vision.attach_image": vision.attach_image_authorized,
            "vision.layout": vision.save_layout_authorized,
            "action.create": actions.save_authorized,
            "action.complete": actions.complete_authorized,
            "action.reopen": actions.reopen_authorized,
            "action.link": actions.link_authorized,
            "action.weekly_note": actions.save_note_authorized,
            "sedona.start": sedona.start_authorized,
            "sedona.step": sedona.step_authorized,
            "sedona.resume": sedona.resume_authorized,
            "privacy.delete_all": privacy.delete_all_authorized,
            "ritual.start": rituals.start_authorized,
            "ritual.step": rituals.step_authorized,
            "ritual.resume": rituals.resume_authorized,
            "report.save": reports.save_authorized,
            "backup.restore": backup.restore_authorized,
        },
    )
    mcp_server = build_server(gratitude, vision, actions, sedona, dispatcher)
    mcp = SyncMCPClient(mcp_server)
    clock = SystemClock()
    read_tools = build_read_tools(mcp, clock)
    read_agent = ReadOnlyAgent(read_tools, settings)

    def llm_available() -> bool:
        if not settings.enable_llm:
            return False
        return probe_ollama(settings.ollama_base_url)

    orchestrator = Orchestrator(
        mcp=mcp,
        sedona=sedona,
        read_agent=read_agent,
        llm_available=llm_available,
        skills_dir=settings.skills_dir,
        clock=clock,
    )
    return Container(
        settings=settings,
        db=database,
        auth=auth,
        gratitude=gratitude,
        vision=vision,
        actions=actions,
        sedona=sedona,
        privacy=privacy,
        stats=stats,
        reports=reports,
        rituals=rituals,
        backup=backup,
        voice=voice,
        dispatcher=dispatcher,
        mcp_server=mcp_server,
        mcp=mcp,
        read_tools=read_tools,
        read_agent=read_agent,
        orchestrator=orchestrator,
    )
