"""导出和删除本地数据。删除必须带上用户确认过的固定语句。"""

from __future__ import annotations

import io
import json
import shutil
import zipfile
from pathlib import Path

from innervision.auth import AuthorizationService
from innervision.db import Database
from innervision.images import remove_image
from innervision.models import DeleteAllData
from innervision.services.common import write_audit

EXPORT_TABLES = (
    "gratitude_entries",
    "vision_cards",
    "vision_milestones",
    "action_tasks",
    "weekly_reviews",
    "sedona_sessions",
    "sedona_turns",
    "ritual_sessions",
    "ritual_turns",
    "growth_reports",
)

DELETE_TABLES = (
    "ritual_turns",
    "ritual_sessions",
    "sedona_turns",
    "sedona_sessions",
    "vision_milestones",
    "action_tasks",
    "vision_cards",
    "gratitude_entries",
    "weekly_reviews",
    "growth_reports",
    "pending_writes",
    "audit_log",
)


class PrivacyService:
    """把数据导出为本地压缩包，或在确认后清空本机记录。"""

    def __init__(
        self,
        db: Database,
        auth: AuthorizationService,
        image_dir: Path,
        log_dir: Path,
        backup_dir: Path,
    ) -> None:
        self.db = db
        self.auth = auth
        self.image_dir = image_dir
        self.log_dir = log_dir
        self.backup_dir = backup_dir

    def propose_delete_all(self, confirm_text: str) -> str:
        """校验固定确认句后，才生成清空数据的待确认请求。"""
        data = DeleteAllData(confirm_text=confirm_text)
        return self.auth.propose("privacy.delete_all", data.model_dump())

    def delete_all_authorized(self, token: str) -> dict:
        """核销授权后删除记录、图片和日志文件。"""
        with self.db.session() as conn:
            payload = self.auth.consume(conn, token, "privacy.delete_all")
            DeleteAllData.model_validate(payload)
            for table in DELETE_TABLES:
                conn.execute(f"DELETE FROM {table}")
            write_audit(conn, "privacy.delete_all", "privacy", None, 1)
        self._remove_files()
        return {"ok": True}

    def export_bytes(self) -> bytes:
        """打包用户数据。待确认的写入令牌不会放进导出文件。"""
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            with self.db.session() as conn:
                for table in EXPORT_TABLES:
                    rows = conn.execute(f"SELECT * FROM {table}").fetchall()
                    body = json.dumps([dict(row) for row in rows], ensure_ascii=False, indent=2)
                    archive.writestr(f"{table}.json", body)
            if self.image_dir.exists():
                for path in sorted(self.image_dir.iterdir()):
                    if path.is_file():
                        archive.write(path, f"images/{path.name}")
        return buffer.getvalue()

    def _remove_files(self) -> None:
        if self.image_dir.exists():
            for path in self.image_dir.iterdir():
                if path.is_file():
                    remove_image(self.image_dir, path.name)
        log_file = self.log_dir / "app.log"
        if log_file.exists():
            log_file.write_text("", encoding="utf-8")
        if self.backup_dir.exists():
            for path in self.backup_dir.iterdir():
                if path.is_file() and path.suffix == ".zip" and path.name.startswith("innervision-"):
                    path.unlink()
                elif path.name == ".staging" and path.is_dir():
                    shutil.rmtree(path)
