"""第二阶段：布局、趋势、报告、仪式、备份和可选语音。"""

from __future__ import annotations

import base64
import io
import json
import sqlite3
import zipfile
from datetime import date

import pytest
from pydantic import ValidationError

from innervision.auth import AuthorizationError
from innervision.config import load_settings
from innervision.container import build_container
from innervision.images import image_payload
from innervision.safety import is_safe_report, is_safe_single_question, is_safe_statement, question_count
from innervision.services.backup import INSERT_ORDER
from innervision.services.report import choose_body
from innervision.services.ritual import (
    CLOSE_MESSAGE,
    END_MESSAGE,
    LENGTH_MESSAGE,
    PAUSE_MESSAGE,
    QUESTIONS,
)
from innervision.timeutil import monday_of
from innervision.ui.charts import action_figure, emotion_figure, gratitude_figure
from tests.helpers import commit

SENTINEL = "阶段二哨兵"
PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


def test_board_order_waits_for_confirmation(container) -> None:
    first = commit(container, container.vision.propose_create(_vision("甲卡片")))
    second = commit(container, container.vision.propose_create(_vision("乙卡片")))
    original = [first["id"], second["id"]]
    assert [card["id"] for card in container.vision.list_cards()] == original
    token = container.vision.propose_layout(list(reversed(original)))
    with pytest.raises(AuthorizationError):
        container.vision.save_layout_authorized(token)
    assert [card["id"] for card in container.vision.list_cards()] == original
    commit(container, token)
    assert [card["id"] for card in container.vision.list_cards()] == list(reversed(original))
    with pytest.raises(ValueError):
        container.vision.propose_layout([original[0]])


def test_trends_and_report_omit_diary_text(container) -> None:
    today = date.today()
    week = monday_of(today).isoformat()
    commit(
        container,
        container.gratitude.propose_create(
            {
                "entry_date": today.isoformat(),
                "item_1": SENTINEL,
                "item_2": "热茶",
                "item_3": "散步",
                "reflection": "",
            }
        ),
    )
    created = commit(
        container,
        container.actions.propose_create(
            {"title": "喝水", "detail": "", "priority": "P1", "vision_id": None}
        ),
    )
    commit(container, container.actions.propose_complete(created["id"]))
    started = commit(container, container.sedona.propose_start())
    with container.db.session() as conn:
        conn.execute(
            """
            UPDATE sedona_sessions
            SET intensity_before = 8, intensity_after = 3, feeling_label = ?, state = 'completed'
            WHERE id = ?
            """,
            (SENTINEL, started["id"]),
        )
    series = container.stats.series(today, weeks=4)
    encoded = json.dumps(series, ensure_ascii=False)
    assert SENTINEL not in encoded
    index = series["weeks"].index(week)
    assert series["gratitude_days"][index] == 1
    assert series["p1_done"][index] == 1
    assert series["sedona_before"][index] == 8.0
    assert series["sedona_after"][index] == 3.0
    assert list(action_figure(series).data[0].y) == series["p1_done"]
    assert list(gratitude_figure(series).data[0].y) == series["gratitude_days"]
    assert list(emotion_figure(series).data[0].y) == series["sedona_before"]

    summary = container.reports.summary(week)
    assert SENTINEL not in json.dumps(summary, ensure_ascii=False)
    body, source = choose_body(summary, "我诊断你有抑郁症，这一定会好。")
    assert source == "template"
    assert "诊断" not in body
    assert is_safe_report(body)
    composed, composed_source = container.reports.compose(week)
    assert composed_source == "template"
    assert SENTINEL not in composed
    assert container.reports.get(week) is None
    commit(container, container.reports.propose_save(week, composed, composed_source))
    assert container.reports.get(week)["body"] == composed
    log_path = container.settings.log_dir / "app.log"
    if log_path.exists():
        assert SENTINEL not in log_path.read_text(encoding="utf-8")


def test_ritual_moves_one_question_at_a_time(container) -> None:
    for text in QUESTIONS.values():
        assert question_count(text) == 1
        assert is_safe_single_question(text)
    for text in (CLOSE_MESSAGE, END_MESSAGE, PAUSE_MESSAGE, LENGTH_MESSAGE):
        assert question_count(text) == 0
        assert is_safe_statement(text)

    started = commit(container, container.rituals.propose_start("morning"))
    assert started["state"] == "arrive"
    assert question_count(started["assistant_message"]) == 1
    with pytest.raises(ValueError):
        container.rituals.propose_start("evening")
    skipped = _ritual_step(container, started["id"], "跳过")
    assert skipped["state"] == "gratitude"
    paused = _ritual_step(container, started["id"], "暂停")
    assert paused["state"] == "paused"
    with pytest.raises(ValueError):
        _ritual_step(container, started["id"], "先停在这里")
    assert container.rituals.get_open()["state"] == "paused"
    resumed = commit(container, container.rituals.propose_resume(started["id"]))
    assert resumed["state"] == "gratitude"
    assert question_count(resumed["assistant_message"]) == 1
    continued = _ritual_step(container, started["id"], "结束这段关系")
    assert continued["state"] == "focus"
    ended = _ritual_step(container, started["id"], "结束")
    assert ended["state"] == "completed"
    assert question_count(ended["assistant_message"]) == 0

    evening = commit(container, container.rituals.propose_start("evening"))
    moved = _ritual_step(container, evening["id"], "不愿意")
    assert moved["state"] == "ease"


def test_ritual_crisis_is_not_stored(container) -> None:
    started = commit(container, container.rituals.propose_start("morning"))
    stopped = _ritual_step(container, started["id"], "我不想活了")
    assert stopped["state"] == "safety_stop"
    assert all("不想活" not in turn["content"] for turn in stopped["turns"])
    with container.db.session() as conn:
        pending = " ".join(row["payload_json"] for row in conn.execute("SELECT payload_json FROM pending_writes"))
    assert "不想活" not in pending
    log_path = container.settings.log_dir / "app.log"
    assert "不想活" not in log_path.read_text(encoding="utf-8")


def test_backup_roundtrip_and_rejection(container) -> None:
    payload = _vision("海边散步")
    payload["milestones"] = ["走到海边"]
    payload.update(image_payload("dot.png", PNG))
    created = commit(container, container.vision.propose_create(payload))
    commit(
        container,
        container.gratitude.propose_create(
            {
                "entry_date": date.today().isoformat(),
                "item_1": SENTINEL,
                "item_2": "热茶",
                "item_3": "散步",
                "reflection": "",
            }
        ),
    )
    image_name = container.vision.get_card(created["id"])["image_name"]
    backup = container.backup.create()
    assert backup.parent == container.settings.backup_dir
    with zipfile.ZipFile(backup) as archive:
        assert "manifest.json" in archive.namelist()
        assert f"images/{image_name}" in archive.namelist()
    commit(container, container.vision.propose_delete(created["id"]))
    gratitude = container.gratitude.list_recent(3)[0]
    commit(container, container.gratitude.propose_delete(int(gratitude["id"])))
    assert container.vision.list_cards() == []
    assert container.vision.image_file(image_name) is None

    bad = container.settings.backup_dir / "innervision-20261104-120000.zip"
    with zipfile.ZipFile(bad, "w") as archive:
        archive.writestr("../evil.txt", "nope")
    with pytest.raises(ValueError):
        commit(container, container.backup.propose_restore(bad.name, "恢复本地备份"))
    assert container.vision.list_cards() == []

    broken = container.settings.backup_dir / "innervision-20261104-120001.zip"
    with zipfile.ZipFile(broken, "w") as archive:
        archive.writestr(
            "manifest.json",
            json.dumps({"format": "innervision-backup", "version": 2}),
        )
        for table in INSERT_ORDER:
            archive.writestr(f"{table}.json", "[]")
        archive.writestr("images/evil.png", b"not-an-image")
    with pytest.raises(ValueError):
        commit(container, container.backup.propose_restore(broken.name, "恢复本地备份"))
    with pytest.raises(ValidationError):
        container.backup.propose_restore(backup.name, "恢复一下")

    commit(container, container.backup.propose_restore(backup.name, "恢复本地备份"))
    restored = container.vision.list_cards()
    assert restored[0]["title"] == "海边散步"
    assert restored[0]["milestones"][0]["title"] == "走到海边"
    assert container.vision.image_file(restored[0]["image_name"]).read_bytes() == PNG
    assert container.gratitude.list_recent(3)[0]["item_1"] == SENTINEL
    log_text = (container.settings.log_dir / "app.log").read_text(encoding="utf-8")
    assert SENTINEL not in log_text


def test_delete_all_removes_backups(container) -> None:
    path = container.backup.create()
    assert path.exists()
    commit(container, container.privacy.propose_delete_all("删除全部本地数据"))
    assert not path.exists()
    with zipfile.ZipFile(io.BytesIO(container.privacy.export_bytes())) as archive:
        assert "growth_reports.json" in archive.namelist()
        assert "ritual_sessions.json" in archive.namelist()


def test_voice_stays_optional(container) -> None:
    assert container.voice.available() is False
    with pytest.raises(ValueError):
        container.voice.transcribe(b"abc", ".wav")
    container.voice.engine = lambda _audio, _suffix: SENTINEL
    assert container.voice.transcribe(b"abc", ".webm") == SENTINEL
    with pytest.raises(ValueError):
        container.voice.transcribe(b"abc", ".exe")
    log_path = container.settings.log_dir / "app.log"
    if log_path.exists():
        assert SENTINEL not in log_path.read_text(encoding="utf-8")


def test_old_database_gains_board_order(tmp_path, monkeypatch) -> None:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    db_path = data_dir / "innervision.db"
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        CREATE TABLE vision_cards (
            id INTEGER PRIMARY KEY,
            title TEXT NOT NULL,
            description TEXT NOT NULL DEFAULT '',
            category TEXT NOT NULL,
            target_date TEXT,
            image_name TEXT,
            status TEXT NOT NULL DEFAULT 'active',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        INSERT INTO vision_cards(title, category, created_at, updated_at)
        VALUES ('旧卡片', '健康', '2026-11-01T00:00:00', '2026-11-01T00:00:00')
        """
    )
    conn.commit()
    conn.close()
    monkeypatch.setenv("INNERVISION_DATA_DIR", str(data_dir))
    monkeypatch.setenv("INNERVISION_ENABLE_LLM", "0")
    built = build_container(load_settings())
    cards = built.vision.list_cards()
    assert cards[0]["title"] == "旧卡片"
    assert cards[0]["board_order"] == 1


def _vision(title: str) -> dict:
    return {
        "title": title,
        "description": "",
        "category": "健康",
        "target_date": None,
        "milestones": [],
        "image_base64": "",
        "image_ext": "",
    }


def _ritual_step(container, session_id: int, text: str) -> dict:
    return commit(container, container.rituals.propose_step(session_id, text))
