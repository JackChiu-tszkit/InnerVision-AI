"""每周成长报告。数字来自本地记录，叙述可以由本地模型改写。"""

from __future__ import annotations

import json

from innervision.agent.prompts import load_skill, probe_ollama
from innervision.auth import AuthorizationService
from innervision.config import Settings
from innervision.db import Database
from innervision.logging_setup import safe_log
from innervision.models import GrowthReportSave
from innervision.safety import is_safe_report
from innervision.services.common import write_audit
from innervision.timeutil import now_iso, parse_date, week_end


class ReportService:
    """生成并保存一周的数量回顾。保存前必须经过用户确认。"""

    def __init__(self, db: Database, auth: AuthorizationService, settings: Settings) -> None:
        self.db = db
        self.auth = auth
        self.settings = settings

    def summary(self, week_start: str) -> dict:
        """汇总这一周的数量。查询不读取感恩正文或感受原文。"""
        start = parse_date(week_start)
        if start.weekday() != 0:
            raise ValueError("周报告必须从周一开始")
        end = week_end(start)
        start_s = start.isoformat()
        end_s = end.isoformat()
        with self.db.session() as conn:
            gratitude = conn.execute(
                """
                SELECT COUNT(*) AS n FROM gratitude_entries
                WHERE entry_date >= ? AND entry_date <= ?
                """,
                (start_s, end_s),
            ).fetchone()["n"]
            actions = conn.execute(
                """
                SELECT priority, COUNT(*) AS n FROM action_tasks
                WHERE status = 'done'
                  AND substr(completed_at, 1, 10) >= ?
                  AND substr(completed_at, 1, 10) <= ?
                GROUP BY priority
                """,
                (start_s, end_s),
            ).fetchall()
            sedona = conn.execute(
                """
                SELECT COUNT(*) AS n FROM sedona_sessions
                WHERE substr(created_at, 1, 10) >= ? AND substr(created_at, 1, 10) <= ?
                """,
                (start_s, end_s),
            ).fetchone()["n"]
            rituals = conn.execute(
                """
                SELECT kind, COUNT(*) AS n FROM ritual_sessions
                WHERE state = 'completed' AND ritual_date >= ? AND ritual_date <= ?
                GROUP BY kind
                """,
                (start_s, end_s),
            ).fetchall()
        action_counts = {row["priority"]: int(row["n"]) for row in actions}
        ritual_counts = {row["kind"]: int(row["n"]) for row in rituals}
        return {
            "week_start": start_s,
            "week_end": end.isoformat(),
            "gratitude_days": int(gratitude),
            "p1_done": action_counts.get("P1", 0),
            "p2_done": action_counts.get("P2", 0),
            "sedona_sessions": int(sedona),
            "ritual_morning": ritual_counts.get("morning", 0),
            "ritual_evening": ritual_counts.get("evening", 0),
        }

    def compose(self, week_start: str) -> tuple[str, str]:
        """先产出模板。本地模型若越界或不可用，就保留模板。"""
        summary = self.summary(week_start)
        return choose_body(summary, self._ask_model(summary))

    def propose_save(self, week_start: str, body: str, source: str) -> str:
        """校验报告后再生成待确认的保存请求。"""
        data = GrowthReportSave(week_start=week_start, body=body, source=source)
        return self.auth.propose("report.save", data.model_dump())

    def save_authorized(self, token: str) -> dict:
        """核销授权后按周保存报告。同一周再次保存会覆盖旧稿。"""
        with self.db.session() as conn:
            payload = self.auth.consume(conn, token, "report.save")
            data = GrowthReportSave.model_validate(payload)
            moment = now_iso()
            conn.execute(
                """
                INSERT INTO growth_reports(week_start, body, source, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(week_start) DO UPDATE SET
                    body = excluded.body,
                    source = excluded.source,
                    updated_at = excluded.updated_at
                """,
                (data.week_start, data.body, data.source, moment, moment),
            )
            write_audit(conn, "report.save", "report", None, 1)
        return {"ok": True, "week_start": data.week_start}

    def get(self, week_start: str) -> dict | None:
        """读取已经保存的一周报告。"""
        with self.db.session() as conn:
            row = conn.execute(
                """
                SELECT week_start, body, source, updated_at
                FROM growth_reports WHERE week_start = ?
                """,
                (week_start,),
            ).fetchone()
        return dict(row) if row else None

    def _ask_model(self, summary: dict) -> str | None:
        """只把数量交给本地模型。失败时返回空，由模板接手。"""
        if not self.settings.enable_llm or not probe_ollama(self.settings.ollama_base_url):
            return None
        try:
            from langchain_core.messages import HumanMessage, SystemMessage
            from langchain_ollama import ChatOllama

            skill = ""
            try:
                skill = load_skill(self.settings.skills_dir, "report")
            except ValueError:
                skill = ""
            model = ChatOllama(
                model=self.settings.ollama_model,
                base_url=self.settings.ollama_base_url,
                temperature=0.2,
                num_predict=220,
                timeout=8,
            )
            response = model.invoke(
                [
                    SystemMessage(
                        content=(
                            "你只根据给出的数字写一段不超过180字的中文回顾。"
                            "不要提问。不要诊断。不要承诺显化、疗愈或任何结果。"
                            "不要编造数字以外的生活细节。\n"
                            + skill
                        )
                    ),
                    HumanMessage(content=json.dumps(summary, ensure_ascii=False)),
                ]
            )
            content = response.content
            return content.strip() if isinstance(content, str) else None
        except Exception:
            safe_log("report_fallback", reason="llm_error", ok=0)
            return None


def render_template(summary: dict) -> str:
    """用数量写一段固定回顾，不引用日记或感受原文。"""
    return (
        f"这一周从 {summary['week_start']} 到 {summary['week_end']}。"
        f"你记下了 {summary['gratitude_days']} 天感恩，"
        f"完成了 {summary['p1_done']} 项紧急且重要的行动，"
        f"以及 {summary['p2_done']} 项面向以后的行动。"
        f"释放练习做了 {summary['sedona_sessions']} 次，"
        f"晨间仪式 {summary['ritual_morning']} 次，晚间仪式 {summary['ritual_evening']} 次。"
        f"这些数字只是记录，不代表必须出现某种变化。"
    )


def choose_body(summary: dict, drafted: str | None) -> tuple[str, str]:
    """模型文案越界时退回模板。"""
    template = render_template(summary)
    if drafted and is_safe_report(drafted):
        return drafted.strip(), "local_model"
    return template, "template"
