"""授权、感恩、愿景、行动、释放练习和隐私。"""

from __future__ import annotations

import base64
import io
import zipfile

import pytest
from pydantic import ValidationError

from innervision.auth import AuthorizationError
from innervision.images import resolve_image
from innervision.safety import (
    is_crisis_text,
    is_safe_single_question,
    is_safe_statement,
    question_count,
    support_message,
)
from innervision.services.sedona import QUESTIONS, TemplatePhraser
from tests.helpers import commit

SENTINEL = "哨兵阳光不该出现在日志"
PNG_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


def _gratitude_payload(item: str = SENTINEL) -> dict:
    return {
        "entry_date": "2026-11-04",
        "item_1": item,
        "item_2": "热茶",
        "item_3": "散步",
        "reflection": "",
    }


def test_unconfirmed_write_is_rejected(container) -> None:
    token = container.gratitude.propose_create(_gratitude_payload())
    with pytest.raises(AuthorizationError):
        container.gratitude.save_authorized(token)
    assert container.gratitude.list_recent() == []


def test_expired_confirmation_is_rejected(container) -> None:
    token = container.auth.propose("gratitude.create", _gratitude_payload(), ttl_seconds=-5)
    with pytest.raises(AuthorizationError):
        container.auth.confirm(token)


def test_tampered_payload_is_rejected(container) -> None:
    token = container.gratitude.propose_create(_gratitude_payload())
    container.auth.confirm(token)
    with container.db.session() as conn:
        conn.execute(
            "UPDATE pending_writes SET payload_json = ? WHERE token = ?",
            ('{"entry_date":"2026-11-04","item_1":"被换掉","item_2":"热茶","item_3":"散步","reflection":""}', token),
        )
    with pytest.raises(AuthorizationError):
        container.dispatcher.commit(token)


def test_gratitude_upsert_and_weekly_review(container) -> None:
    first = commit(container, container.gratitude.propose_create(_gratitude_payload("清晨")))
    second = commit(container, container.gratitude.propose_create(_gratitude_payload("傍晚")))
    assert first["id"] == second["id"]
    review = container.gratitude.weekly_review("2026-11-04")
    assert review["week_start"] == "2026-11-02"
    assert review["entry_count"] == 1
    assert review["items"][0] == "傍晚"
    assert "保证" not in review["narrative"]
    assert "诊断" not in review["narrative"]


def test_vision_image_milestone_and_path_guard(container, tmp_path) -> None:
    encoded = base64.b64encode(PNG_BYTES).decode("ascii")
    token = container.vision.propose_create(
        {
            "title": "每天散步",
            "category": "健康",
            "description": "沿着河边走",
            "target_date": "2026-12-31",
            "milestones": ["迈出一步"],
            "image_base64": encoded,
            "image_ext": ".png",
        }
    )
    created = commit(container, token)
    card = container.vision.get_card(created["id"])
    assert card["milestones"][0]["title"] == "迈出一步"
    assert container.vision.image_file(card["image_name"]) is not None
    toggled = commit(
        container,
        container.vision.propose_toggle(card["milestones"][0]["id"], True),
    )
    assert toggled["done"] is True
    with pytest.raises(ValueError):
        container.vision.propose_create(
            {
                "title": "坏图片",
                "category": "其他",
                "description": "",
                "target_date": None,
                "milestones": [],
                "image_base64": base64.b64encode(b"hello").decode("ascii"),
                "image_ext": ".png",
            }
        )
    with pytest.raises(ValueError):
        resolve_image(tmp_path, "../secret.png")
    image_name = card["image_name"]
    commit(container, container.vision.propose_delete(created["id"]))
    assert container.vision.get_card(created["id"]) is None
    assert not (container.settings.image_dir / image_name).exists()


def test_actions_priority_completion_and_weekly_note(container) -> None:
    p1 = commit(
        container,
        container.actions.propose_create(
            {"title": "给妈妈打电话", "priority": "P1", "detail": "", "vision_id": None}
        ),
    )
    commit(
        container,
        container.actions.propose_create(
            {"title": "整理书架", "priority": "P2", "detail": "", "vision_id": None}
        ),
    )
    commit(container, container.actions.propose_complete(p1["id"]))
    review = container.actions.weekly_review("2026-11-04")
    assert review["p1_done"] == 1
    assert review["p2_done"] == 0
    assert review["open_count"] == 1
    assert "保证" not in review["narrative"]
    commit(container, container.actions.propose_weekly_note("2026-11-02", "这周先打完电话"))
    assert container.actions.weekly_review("2026-11-04")["notes"] == "这周先打完电话"
    with pytest.raises(ValidationError):
        container.actions.propose_weekly_note("2026-11-04", "不是周一")


def test_sedona_release_is_optional_and_one_question(container) -> None:
    started = commit(container, container.sedona.propose_start())
    session_id = started["id"]
    assert started["state"] == "intake"
    assert question_count(started["assistant_message"]) == 1

    located = _step(container, session_id, "胸口发紧")
    assert located["state"] == "locate"
    body = _step(container, session_id, "胸口，强度 6")
    assert body["state"] == "allow"
    assert body["intensity_before"] == 6
    allowed = _step(container, session_id, "我愿意先让它在这里")
    assert allowed["state"] == "could"
    could = _step(container, session_id, "也许不能")
    assert could["state"] == "would"
    would = _step(container, session_id, "不愿意")
    assert would["state"] == "reflect"
    assert would["when_answer"] == ""
    assert question_count(would["assistant_message"]) == 1
    done = _step(container, session_id, "轻一点，强度 3")
    assert done["state"] == "completed"
    assert done["intensity_after"] == 3
    assert question_count(done["assistant_message"]) == 0
    assert "必须放下" not in done["assistant_message"]


def test_sedona_pause_skip_end_and_bad_rephrase(container) -> None:
    container.sedona.phraser = _BadPhraser()
    started = commit(container, container.sedona.propose_start())
    session_id = started["id"]
    assert started["assistant_message"] == QUESTIONS["intake"]
    paused = _step(container, session_id, "暂停")
    assert paused["state"] == "paused"
    assert question_count(paused["assistant_message"]) == 0
    resumed = commit(container, container.sedona.propose_resume(session_id))
    assert resumed["state"] == "intake"
    skipped = _step(container, session_id, "跳过")
    assert skipped["state"] == "locate"
    assert skipped["assistant_message"] == QUESTIONS["locate"]
    ended = _step(container, session_id, "结束")
    assert ended["state"] == "completed"
    assert question_count(ended["assistant_message"]) == 0


def test_relationship_ending_is_not_a_crisis(container) -> None:
    assert is_crisis_text("我今天很难过，胸口发紧") is False
    assert is_crisis_text("我想结束这段关系") is False
    started = commit(container, container.sedona.propose_start())
    stepped = _step(container, started["id"], "我想结束这段关系")
    assert stepped["state"] == "locate"
    assert stepped["safety_stopped"] == 0


def test_crisis_stops_without_storing_or_rephrasing(container) -> None:
    container.sedona.phraser = _ExplodingPhraser()
    started = commit(container, container.sedona.propose_start())
    # 开始练习时的问句在替换 phraser 之前已经生成。
    container.sedona.phraser = _ExplodingPhraser()
    stopped = _step(container, started["id"], "我不想活了")
    assert stopped["state"] == "safety_stop"
    assert stopped["safety_stopped"] == 1
    assert "12356" in stopped["assistant_message"]
    assert "112" in stopped["assistant_message"]
    assert question_count(stopped["assistant_message"]) == 0
    assert is_safe_statement(support_message())
    combined = " ".join(turn["content"] for turn in stopped["turns"])
    assert "不想活了" not in combined
    with container.db.session() as conn:
        pending = conn.execute("SELECT payload_json FROM pending_writes").fetchall()
    assert all("不想活了" not in row["payload_json"] for row in pending)


def test_long_session_ends_gently(container) -> None:
    started = commit(container, container.sedona.propose_start())
    with container.db.session() as conn:
        conn.execute(
            "UPDATE sedona_sessions SET turn_count = 40 WHERE id = ?",
            (started["id"],),
        )
    ended = _step(container, started["id"], "还在这里")
    assert ended["state"] == "completed"
    assert "比较长" in ended["assistant_message"]
    assert question_count(ended["assistant_message"]) == 0


def test_question_guard_and_templates() -> None:
    assert is_safe_single_question("你现在愿意看看它吗？")
    assert not is_safe_single_question("你怎么样？还有呢？")
    assert not is_safe_single_question("你必须放下它吗？")
    for template in QUESTIONS.values():
        assert question_count(template) == 1
        assert is_safe_single_question(template)
    assert TemplatePhraser().phrase("只问这一句？", "intake") == "只问这一句？"


def test_privacy_export_and_delete(container) -> None:
    commit(container, container.gratitude.propose_create(_gratitude_payload()))
    exported = container.privacy.export_bytes()
    with zipfile.ZipFile(io.BytesIO(exported)) as archive:
        diary = archive.read("gratitude_entries.json").decode("utf-8")
    assert SENTINEL in diary
    with pytest.raises(ValidationError):
        container.privacy.propose_delete_all("删掉")
    commit(container, container.privacy.propose_delete_all("删除全部本地数据"))
    assert container.gratitude.list_recent() == []
    with zipfile.ZipFile(io.BytesIO(container.privacy.export_bytes())) as archive:
        diary = archive.read("gratitude_entries.json").decode("utf-8")
    assert SENTINEL not in diary


def test_diary_text_is_not_logged(container) -> None:
    token = container.gratitude.propose_create(_gratitude_payload())
    commit(container, token)
    container.orchestrator.handle("看看本周感恩回顾")
    log_text = (container.settings.log_dir / "app.log").read_text(encoding="utf-8")
    assert SENTINEL not in log_text
    with container.db.session() as conn:
        audits = " ".join(row["action"] for row in conn.execute("SELECT action FROM audit_log"))
        pending = " ".join(row["payload_json"] for row in conn.execute("SELECT payload_json FROM pending_writes"))
    assert SENTINEL not in audits
    assert SENTINEL not in pending


def _step(container, session_id: int, text: str) -> dict:
    return commit(container, container.sedona.propose_step(session_id, text))


class _BadPhraser:
    def phrase(self, template: str, step: str) -> str:
        return "你怎么样？还有呢？"


class _ExplodingPhraser:
    def phrase(self, template: str, step: str) -> str:
        raise AssertionError("安全停止时不应该改写问句")
