"""SQLite 连接与表结构。数据库文件只放在本机数据目录。"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS gratitude_entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entry_date TEXT NOT NULL UNIQUE,
    item_1 TEXT NOT NULL,
    item_2 TEXT NOT NULL,
    item_3 TEXT NOT NULL,
    reflection TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS vision_cards (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    category TEXT NOT NULL,
    target_date TEXT,
    image_name TEXT,
    status TEXT NOT NULL DEFAULT 'active',
    board_order INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS vision_milestones (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    vision_id INTEGER NOT NULL REFERENCES vision_cards(id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    done INTEGER NOT NULL DEFAULT 0,
    sort_order INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS action_tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    detail TEXT NOT NULL DEFAULT '',
    priority TEXT NOT NULL CHECK (priority IN ('P1', 'P2')),
    status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'done')),
    vision_id INTEGER REFERENCES vision_cards(id) ON DELETE SET NULL,
    created_at TEXT NOT NULL,
    completed_at TEXT
);

CREATE TABLE IF NOT EXISTS weekly_reviews (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    week_start TEXT NOT NULL UNIQUE,
    notes TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sedona_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    state TEXT NOT NULL,
    previous_state TEXT,
    feeling_label TEXT NOT NULL DEFAULT '',
    body_note TEXT NOT NULL DEFAULT '',
    intensity_before INTEGER,
    intensity_after INTEGER,
    allow_answer TEXT NOT NULL DEFAULT '',
    could_answer TEXT NOT NULL DEFAULT '',
    would_answer TEXT NOT NULL DEFAULT '',
    when_answer TEXT NOT NULL DEFAULT '',
    reflection TEXT NOT NULL DEFAULT '',
    safety_stopped INTEGER NOT NULL DEFAULT 0,
    turn_count INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sedona_turns (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL REFERENCES sedona_sessions(id) ON DELETE CASCADE,
    state TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
    content TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ritual_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL CHECK (kind IN ('morning', 'evening')),
    ritual_date TEXT NOT NULL,
    state TEXT NOT NULL,
    previous_state TEXT,
    safety_stopped INTEGER NOT NULL DEFAULT 0,
    turn_count INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ritual_turns (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL REFERENCES ritual_sessions(id) ON DELETE CASCADE,
    state TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
    content TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS growth_reports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    week_start TEXT NOT NULL UNIQUE,
    body TEXT NOT NULL,
    source TEXT NOT NULL CHECK (source IN ('template', 'local_model')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS pending_writes (
    token TEXT PRIMARY KEY,
    action TEXT NOT NULL,
    payload_hash TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    confirmed INTEGER NOT NULL DEFAULT 0,
    consumed INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    action TEXT NOT NULL,
    entity TEXT NOT NULL,
    entity_id INTEGER,
    ok INTEGER NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_gratitude_date ON gratitude_entries(entry_date);
CREATE INDEX IF NOT EXISTS idx_action_status ON action_tasks(status, priority);
CREATE INDEX IF NOT EXISTS idx_sedona_state ON sedona_sessions(state);
CREATE INDEX IF NOT EXISTS idx_turns_session ON sedona_turns(session_id);
CREATE INDEX IF NOT EXISTS idx_ritual_date ON ritual_sessions(ritual_date, kind);
CREATE INDEX IF NOT EXISTS idx_ritual_turns ON ritual_turns(session_id);
CREATE INDEX IF NOT EXISTS idx_report_week ON growth_reports(week_start);
"""


class Database:
    """管理 SQLite 连接。每次业务操作使用独立事务。"""

    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.session() as conn:
            conn.executescript(SCHEMA)
            self._migrate(conn)

    def _migrate(self, conn: sqlite3.Connection) -> None:
        """给已有数据库补上新列和新索引。新建库已经包含这些列。"""
        columns = {row[1] for row in conn.execute("PRAGMA table_info(vision_cards)")}
        if "board_order" not in columns:
            conn.execute(
                "ALTER TABLE vision_cards ADD COLUMN board_order INTEGER NOT NULL DEFAULT 0"
            )
            conn.execute("UPDATE vision_cards SET board_order = id")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_vision_board ON vision_cards(board_order, id)"
        )
        conn.execute(
            "INSERT INTO schema_meta(key, value) VALUES('version', '2') "
            "ON CONFLICT(key) DO UPDATE SET value = '2'"
        )

    def connect(self) -> sqlite3.Connection:
        """打开连接并启用外键。调用方负责关闭。"""
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    @contextmanager
    def session(self) -> Iterator[sqlite3.Connection]:
        """提交成功的写入；出现异常时回滚，避免留下半条记录。"""
        conn = self.connect()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
