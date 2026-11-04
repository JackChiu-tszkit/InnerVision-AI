"""本地备份与恢复。恢复会覆盖当前记录，因此必须经过确认。"""

from __future__ import annotations

import json
import re
import sqlite3
import zipfile
from datetime import datetime
from pathlib import Path

from innervision.auth import AuthorizationService
from innervision.db import Database
from innervision.images import MAX_IMAGE_BYTES, decode_image, remove_image, resolve_image
from innervision.logging_setup import safe_log
from innervision.models import RestoreBackup
from innervision.services.common import write_audit

BACKUP_NAME_RE = re.compile(r"^innervision-\d{8}-\d{6}\.zip$")
_IDENT = re.compile(r"^[a-z0-9_]+$")
_IMAGE_NAME = re.compile(r"^[a-f0-9]{32}\.(png|jpg|jpeg|webp|gif)$")
_MAX_UNCOMPRESSED = 80 * 1024 * 1024
_MAX_IMAGES = 40 * 1024 * 1024

COLUMNS = {
    "gratitude_entries": (
        "id", "entry_date", "item_1", "item_2", "item_3", "reflection", "created_at", "updated_at",
    ),
    "vision_cards": (
        "id", "title", "description", "category", "target_date", "image_name", "status",
        "board_order", "created_at", "updated_at",
    ),
    "vision_milestones": ("id", "vision_id", "title", "done", "sort_order"),
    "action_tasks": (
        "id", "title", "detail", "priority", "status", "vision_id", "created_at", "completed_at",
    ),
    "weekly_reviews": ("id", "week_start", "notes", "created_at", "updated_at"),
    "sedona_sessions": (
        "id", "state", "previous_state", "feeling_label", "body_note", "intensity_before",
        "intensity_after", "allow_answer", "could_answer", "would_answer", "when_answer",
        "reflection", "safety_stopped", "turn_count", "created_at", "updated_at",
    ),
    "sedona_turns": ("id", "session_id", "state", "role", "content", "created_at"),
    "ritual_sessions": (
        "id", "kind", "ritual_date", "state", "previous_state", "safety_stopped",
        "turn_count", "created_at", "updated_at",
    ),
    "ritual_turns": ("id", "session_id", "state", "role", "content", "created_at"),
    "growth_reports": ("id", "week_start", "body", "source", "created_at", "updated_at"),
}

INSERT_ORDER = (
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

DELETE_ORDER = (
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
)


class BackupService:
    """把本机数据打成带时间的压缩包，并在确认后恢复。"""

    def __init__(self, db: Database, auth: AuthorizationService, image_dir: Path, backup_dir: Path) -> None:
        self.db = db
        self.auth = auth
        self.image_dir = image_dir
        self.backup_dir = backup_dir

    def create(self) -> Path:
        """写一份新的本地备份。这个动作由用户按钮直接发起。"""
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        name = f"innervision-{datetime.now().strftime('%Y%m%d-%H%M%S')}.zip"
        if not BACKUP_NAME_RE.fullmatch(name):
            raise ValueError("备份文件名无效")
        target = self._path_for(name, must_exist=False)
        if target.exists():
            raise ValueError("请稍后再创建备份")
        with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr(
                "manifest.json",
                json.dumps(
                    {"format": "innervision-backup", "version": 2, "tables": list(INSERT_ORDER)},
                    ensure_ascii=False,
                ),
            )
            with self.db.session() as conn:
                for table in INSERT_ORDER:
                    rows = conn.execute(f"SELECT * FROM {_table(table)}").fetchall()
                    body = json.dumps([dict(row) for row in rows], ensure_ascii=False)
                    archive.writestr(f"{table}.json", body)
            if self.image_dir.exists():
                for path in sorted(self.image_dir.iterdir()):
                    if not path.is_file():
                        continue
                    try:
                        resolve_image(self.image_dir, path.name)
                    except ValueError:
                        continue
                    archive.write(path, f"images/{path.name}")
        safe_log("backup_created", ok=1)
        return target

    def list_backups(self) -> list[dict]:
        """列出数据目录里的备份文件名和大小。"""
        if not self.backup_dir.exists():
            return []
        found = []
        for path in self.backup_dir.iterdir():
            if path.is_file() and BACKUP_NAME_RE.fullmatch(path.name):
                found.append({"name": path.name, "size": path.stat().st_size})
        found.sort(key=lambda item: item["name"], reverse=True)
        return found

    def propose_restore(self, backup_name: str, confirm_text: str) -> str:
        """校验固定确认句和文件名后，才生成恢复请求。"""
        data = RestoreBackup(backup_name=backup_name, confirm_text=confirm_text)
        self._path_for(data.backup_name, must_exist=True)
        return self.auth.propose("backup.restore", data.model_dump())

    def restore_authorized(self, token: str) -> dict:
        """核销授权后用备份覆盖当前记录和图片。"""
        with self.db.session() as conn:
            payload = self.auth.consume(conn, token, "backup.restore")
            data = RestoreBackup.model_validate(payload)
            path = self._path_for(data.backup_name, must_exist=True)
            tables, images = _read_backup(path)
            try:
                for table in DELETE_ORDER:
                    conn.execute(f"DELETE FROM {_table(table)}")
                for table in INSERT_ORDER:
                    _insert_rows(conn, table, tables[table])
                    _reset_sequence(conn, table, tables[table])
            except sqlite3.IntegrityError:
                raise ValueError("备份内容无法恢复") from None
            write_audit(conn, "backup.restore", "backup", None, 1)
        self._replace_images(images)
        safe_log("backup_restored", ok=1)
        return {"ok": True}

    def _path_for(self, name: str, *, must_exist: bool) -> Path:
        if BACKUP_NAME_RE.fullmatch(name) is None:
            raise ValueError("备份文件名无效")
        root = self.backup_dir.resolve()
        target = (root / name).resolve()
        if target.parent != root:
            raise ValueError("备份文件名无效")
        if must_exist and not target.is_file():
            raise ValueError("没有找到这份备份")
        return target

    def _replace_images(self, images: dict[str, bytes]) -> None:
        self.image_dir.mkdir(parents=True, exist_ok=True)
        for path in list(self.image_dir.iterdir()):
            if path.is_file() and path.name not in images:
                remove_image(self.image_dir, path.name)
        for name, blob in images.items():
            resolve_image(self.image_dir, name).write_bytes(blob)


def _read_backup(path: Path) -> tuple[dict[str, list[dict]], dict[str, bytes]]:
    """检查压缩包成员，拒绝路径穿越和无法识别的文件。"""
    try:
        archive = zipfile.ZipFile(path)
    except zipfile.BadZipFile as exc:
        raise ValueError("备份文件无效") from exc
    with archive:
        infos = archive.infolist()
        if sum(info.file_size for info in infos) > _MAX_UNCOMPRESSED:
            raise ValueError("备份文件过大")
        tables = {table: [] for table in INSERT_ORDER}
        images: dict[str, bytes] = {}
        seen_tables: set[str] = set()
        manifest_ok = False
        try:
            members = [(info, _safe_member(info.filename), archive.read(info)) for info in infos]
        except ValueError:
            raise
        except Exception as exc:
            raise ValueError("备份文件无效") from exc
        for _info, member, blob in members:
            if member == "manifest.json":
                try:
                    manifest_ok = _manifest_ok(json.loads(blob))
                except json.JSONDecodeError as exc:
                    raise ValueError("备份文件无效") from exc
                continue
            if member.endswith(".json"):
                table = member[:-5]
                if table not in COLUMNS or table in seen_tables:
                    raise ValueError("备份文件无效")
                seen_tables.add(table)
                try:
                    tables[table] = _rows(json.loads(blob), table)
                except json.JSONDecodeError as exc:
                    raise ValueError("备份文件无效") from exc
                continue
            if member.startswith("images/"):
                name = member.split("/", 1)[1]
                if len(blob) > MAX_IMAGE_BYTES or sum(len(item) for item in images.values()) + len(blob) > _MAX_IMAGES:
                    raise ValueError("备份文件过大")
                if _IMAGE_NAME.fullmatch(name) is None:
                    raise ValueError("备份文件无效")
                decode_image(_b64(blob), Path(name).suffix)
                images[name] = blob
                continue
            raise ValueError("备份文件无效")
        if not manifest_ok or seen_tables != set(INSERT_ORDER):
            raise ValueError("备份文件无效")
        return tables, images


def _manifest_ok(payload: object) -> bool:
    if not isinstance(payload, dict):
        return False
    return payload.get("format") == "innervision-backup" and payload.get("version") == 2


def _rows(payload: object, table: str) -> list[dict]:
    if not isinstance(payload, list):
        raise ValueError("备份文件无效")
    columns = COLUMNS[table]
    cleaned = []
    for item in payload:
        if not isinstance(item, dict) or any(column not in item for column in columns):
            raise ValueError("备份内容无法恢复")
        cleaned.append({column: item[column] for column in columns})
    return cleaned


def _insert_rows(conn: sqlite3.Connection, table: str, rows: list[dict]) -> None:
    columns = COLUMNS[_table(table)]
    if not rows:
        return
    names = ", ".join(_column(table, column) for column in columns)
    marks = ", ".join("?" for _ in columns)
    conn.executemany(
        f"INSERT INTO {_table(table)} ({names}) VALUES ({marks})",
        [tuple(row[column] for column in columns) for row in rows],
    )


def _reset_sequence(conn: sqlite3.Connection, table: str, rows: list[dict]) -> None:
    exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'sqlite_sequence'"
    ).fetchone()
    if exists is None:
        return
    highest = max((int(row["id"]) for row in rows), default=0)
    conn.execute("DELETE FROM sqlite_sequence WHERE name = ?", (table,))
    if highest:
        conn.execute(
            "INSERT INTO sqlite_sequence(name, seq) VALUES (?, ?)",
            (table, highest),
        )


def _safe_member(name: str) -> str:
    cleaned = name.replace("\\", "/")
    if not cleaned or cleaned.startswith("/") or cleaned.endswith("/"):
        raise ValueError("备份文件无效")
    parts = cleaned.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise ValueError("备份文件无效")
    if len(parts) > 2:
        raise ValueError("备份文件无效")
    return cleaned


def _table(name: str) -> str:
    if name not in COLUMNS and name not in DELETE_ORDER:
        raise ValueError("备份内容无法恢复")
    return name


def _column(table: str, name: str) -> str:
    if table not in COLUMNS or name not in COLUMNS[table] or _IDENT.fullmatch(name) is None:
        raise ValueError("备份内容无法恢复")
    return name


def _b64(blob: bytes) -> str:
    import base64

    return base64.b64encode(blob).decode("ascii")
