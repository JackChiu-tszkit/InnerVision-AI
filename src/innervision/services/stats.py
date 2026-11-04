"""情绪与行动趋势。只汇总数量和强度，不返回日记或感受正文。"""

from __future__ import annotations

from datetime import date, timedelta

from innervision.db import Database
from innervision.timeutil import monday_of


class StatsService:
    """为图表准备最近几周的数量。"""

    def __init__(self, db: Database) -> None:
        self.db = db

    def series(self, today: date, weeks: int = 8) -> dict:
        """返回周起点、感恩天数、完成的行动和释放练习强度。"""
        if weeks < 1 or weeks > 52:
            raise ValueError("周数超出范围")
        anchors = _anchors(today, weeks)
        labels = [item.isoformat() for item in anchors]
        gratitude = [0 for _ in anchors]
        p1_done = [0 for _ in anchors]
        p2_done = [0 for _ in anchors]
        before: list[list[int]] = [[] for _ in anchors]
        after: list[list[int]] = [[] for _ in anchors]
        with self.db.session() as conn:
            for row in conn.execute("SELECT entry_date FROM gratitude_entries"):
                index = _index(row["entry_date"], anchors)
                if index is not None:
                    gratitude[index] += 1
            for row in conn.execute(
                "SELECT priority, status, substr(completed_at, 1, 10) AS day FROM action_tasks"
            ):
                if row["status"] != "done":
                    continue
                index = _index(row["day"], anchors)
                if index is None:
                    continue
                if row["priority"] == "P1":
                    p1_done[index] += 1
                elif row["priority"] == "P2":
                    p2_done[index] += 1
            for row in conn.execute(
                """
                SELECT substr(created_at, 1, 10) AS day, intensity_before, intensity_after
                FROM sedona_sessions
                """
            ):
                index = _index(row["day"], anchors)
                if index is None:
                    continue
                if row["intensity_before"] is not None:
                    before[index].append(int(row["intensity_before"]))
                if row["intensity_after"] is not None:
                    after[index].append(int(row["intensity_after"]))
        return {
            "weeks": labels,
            "gratitude_days": gratitude,
            "p1_done": p1_done,
            "p2_done": p2_done,
            "sedona_before": [_average(values) for values in before],
            "sedona_after": [_average(values) for values in after],
        }


def _anchors(today: date, weeks: int) -> list[date]:
    current = monday_of(today)
    start = current - timedelta(weeks=weeks - 1)
    return [start + timedelta(weeks=offset) for offset in range(weeks)]


def _index(value: str | None, anchors: list[date]) -> int | None:
    if not value:
        return None
    try:
        found = date.fromisoformat(str(value)[:10])
    except ValueError:
        return None
    if found < anchors[0] or found > anchors[-1] + timedelta(days=6):
        return None
    return (found - anchors[0]).days // 7


def _average(values: list[int]) -> float | None:
    if not values:
        return None
    return round(sum(values) / len(values), 1)
