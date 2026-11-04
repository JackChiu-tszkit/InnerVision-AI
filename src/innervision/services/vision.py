"""愿景卡片、里程碑和本地图片。"""

from __future__ import annotations

from pathlib import Path

from innervision.auth import AuthorizationService
from innervision.db import Database
from innervision.images import decode_image, remove_image, resolve_image, save_image
from innervision.models import BoardLayout, ImageAttach, MilestoneToggle, VisionCreate, VisionDelete
from innervision.services.common import write_audit
from innervision.timeutil import now_iso


class VisionService:
    """管理愿景卡片。图片文件名由系统生成，不接受任意磁盘路径。"""

    def __init__(self, db: Database, auth: AuthorizationService, image_dir: Path) -> None:
        self.db = db
        self.auth = auth
        self.image_dir = image_dir

    def propose_create(self, payload: dict) -> str:
        """校验愿景内容后，生成待确认的创建请求。"""
        data = VisionCreate.model_validate(payload)
        if data.image_base64:
            decode_image(data.image_base64, data.image_ext)
        return self.auth.propose("vision.create", data.model_dump())

    def propose_delete(self, vision_id: int) -> str:
        """提出删除一张愿景卡片。"""
        data = VisionDelete(vision_id=vision_id)
        return self.auth.propose("vision.delete", data.model_dump())

    def propose_toggle(self, milestone_id: int, done: bool) -> str:
        """提出更新一个里程碑的完成状态。"""
        data = MilestoneToggle(milestone_id=milestone_id, done=done)
        return self.auth.propose("vision.milestone", data.model_dump())

    def propose_attach(self, vision_id: int, image_base64: str, image_ext: str) -> str:
        """提出为已有卡片附上本地图片。"""
        decode_image(image_base64, image_ext)
        data = ImageAttach(vision_id=vision_id, image_base64=image_base64, image_ext=image_ext)
        return self.auth.propose("vision.attach_image", data.model_dump())

    def propose_layout(self, ordered_ids: list[int]) -> str:
        """提出新的愿景板顺序。编号必须和现有卡片完全一致。"""
        data = BoardLayout(ordered_ids=ordered_ids)
        self._assert_same_cards(data.ordered_ids)
        return self.auth.propose("vision.layout", data.model_dump())

    def save_layout_authorized(self, token: str) -> dict:
        """核销授权后按新顺序写回每张卡片的位置。"""
        with self.db.session() as conn:
            payload = self.auth.consume(conn, token, "vision.layout")
            data = BoardLayout.model_validate(payload)
            rows = conn.execute("SELECT id FROM vision_cards").fetchall()
            existing = {int(row["id"]) for row in rows}
            if set(data.ordered_ids) != existing:
                raise ValueError("愿景板顺序和现有卡片不一致")
            moment = now_iso()
            for index, vision_id in enumerate(data.ordered_ids):
                conn.execute(
                    "UPDATE vision_cards SET board_order = ?, updated_at = ? WHERE id = ?",
                    (index, moment, vision_id),
                )
            write_audit(conn, "vision.layout", "vision", None, 1)
        return {"ok": True, "count": len(data.ordered_ids)}

    def save_authorized(self, token: str) -> dict:
        """核销授权后创建卡片、里程碑和可选图片。失败时不会留下图片文件。"""
        image_name: str | None = None
        try:
            with self.db.session() as conn:
                payload = self.auth.consume(conn, token, "vision.create")
                data = VisionCreate.model_validate(payload)
                moment = now_iso()
                if data.image_base64:
                    raw, ext = decode_image(data.image_base64, data.image_ext)
                    image_name = save_image(self.image_dir, raw, ext)
                highest = conn.execute(
                    "SELECT COALESCE(MAX(board_order), -1) AS highest FROM vision_cards"
                ).fetchone()
                board_order = int(highest["highest"]) + 1
                cursor = conn.execute(
                    """
                    INSERT INTO vision_cards(
                        title, description, category, target_date, image_name, status,
                        board_order, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, 'active', ?, ?, ?)
                    """,
                    (
                        data.title,
                        data.description,
                        data.category,
                        data.target_date,
                        image_name,
                        board_order,
                        moment,
                        moment,
                    ),
                )
                vision_id = int(cursor.lastrowid)
                for index, title in enumerate(data.milestones):
                    conn.execute(
                        """
                        INSERT INTO vision_milestones(vision_id, title, done, sort_order)
                        VALUES (?, ?, 0, ?)
                        """,
                        (vision_id, title, index),
                    )
                write_audit(conn, "vision.create", "vision", vision_id, 1)
        except Exception:
            remove_image(self.image_dir, image_name)
            raise
        return {"ok": True, "id": vision_id}

    def delete_authorized(self, token: str) -> dict:
        """删除卡片及其里程碑，并在事务成功后删除图片。"""
        with self.db.session() as conn:
            payload = self.auth.consume(conn, token, "vision.delete")
            data = VisionDelete.model_validate(payload)
            row = conn.execute(
                "SELECT image_name FROM vision_cards WHERE id = ?",
                (data.vision_id,),
            ).fetchone()
            if row is None:
                raise ValueError("没有找到这张愿景卡片")
            image_name = row["image_name"]
            conn.execute("DELETE FROM vision_cards WHERE id = ?", (data.vision_id,))
            write_audit(conn, "vision.delete", "vision", data.vision_id, 1)
        remove_image(self.image_dir, image_name)
        return {"ok": True, "deleted": data.vision_id}

    def toggle_milestone_authorized(self, token: str) -> dict:
        """按确认过的目标状态更新里程碑。"""
        with self.db.session() as conn:
            payload = self.auth.consume(conn, token, "vision.milestone")
            data = MilestoneToggle.model_validate(payload)
            cursor = conn.execute(
                "UPDATE vision_milestones SET done = ? WHERE id = ?",
                (1 if data.done else 0, data.milestone_id),
            )
            if cursor.rowcount != 1:
                raise ValueError("没有找到这个里程碑")
            write_audit(conn, "vision.milestone", "milestone", data.milestone_id, 1)
        return {"ok": True, "milestone_id": data.milestone_id, "done": data.done}

    def attach_image_authorized(self, token: str) -> dict:
        """替换卡片图片。新文件写好后才删掉旧文件。"""
        image_name: str | None = None
        previous: str | None = None
        try:
            with self.db.session() as conn:
                payload = self.auth.consume(conn, token, "vision.attach_image")
                data = ImageAttach.model_validate(payload)
                row = conn.execute(
                    "SELECT image_name FROM vision_cards WHERE id = ?",
                    (data.vision_id,),
                ).fetchone()
                if row is None:
                    raise ValueError("没有找到这张愿景卡片")
                previous = row["image_name"]
                raw, ext = decode_image(data.image_base64, data.image_ext)
                image_name = save_image(self.image_dir, raw, ext)
                conn.execute(
                    "UPDATE vision_cards SET image_name = ?, updated_at = ? WHERE id = ?",
                    (image_name, now_iso(), data.vision_id),
                )
                write_audit(conn, "vision.attach_image", "vision", data.vision_id, 1)
        except Exception:
            remove_image(self.image_dir, image_name)
            raise
        if previous and previous != image_name:
            remove_image(self.image_dir, previous)
        return {"ok": True, "id": data.vision_id, "image_name": image_name}

    def list_cards(self) -> list[dict]:
        """读取全部愿景卡片和里程碑。"""
        with self.db.session() as conn:
            cards = conn.execute(
                "SELECT * FROM vision_cards ORDER BY board_order ASC, id ASC"
            ).fetchall()
            milestones = conn.execute(
                "SELECT * FROM vision_milestones ORDER BY sort_order, id"
            ).fetchall()
        grouped: dict[int, list[dict]] = {}
        for item in milestones:
            grouped.setdefault(int(item["vision_id"]), []).append(dict(item))
        result = []
        for card in cards:
            data = dict(card)
            data["milestones"] = grouped.get(int(card["id"]), [])
            result.append(data)
        return result

    def get_card(self, vision_id: int) -> dict | None:
        """读取一张卡片。不存在时返回空。"""
        for card in self.list_cards():
            if card["id"] == vision_id:
                return card
        return None

    def image_file(self, image_name: str | None) -> Path | None:
        """把安全文件名解析成数据目录中的路径。"""
        if not image_name:
            return None
        path = resolve_image(self.image_dir, image_name)
        if not path.exists():
            return None
        return path

    def _assert_same_cards(self, ordered_ids: list[int]) -> None:
        """顺序必须覆盖每一张现有卡片，不能多也不能少。"""
        with self.db.session() as conn:
            rows = conn.execute("SELECT id FROM vision_cards").fetchall()
        existing = {int(row["id"]) for row in rows}
        if set(ordered_ids) != existing:
            raise ValueError("愿景板顺序和现有卡片不一致")
