"""时间工具。时间戳使用这台电脑的本地时间，方便按用户的日历做周回顾。"""

from __future__ import annotations

from datetime import date, datetime, timedelta


def now_iso() -> str:
    """返回去掉微秒的本地时间字符串。"""
    return datetime.now().replace(microsecond=0).isoformat()


def date_to_iso(value: date) -> str:
    """把日期格式化为 YYYY-MM-DD。"""
    return value.isoformat()


def parse_date(value: str) -> date:
    """解析用户提供的日期。格式不对时抛出 ValueError。"""
    return date.fromisoformat(value)


def monday_of(value: date) -> date:
    """返回该日期所在周的周一，作为周回顾的起点。"""
    return value - timedelta(days=value.weekday())


def week_end(week_start: date) -> date:
    """返回周一对应的周日。"""
    return week_start + timedelta(days=6)


class SystemClock:
    """提供今天的本地日期，测试时可以替换成固定日期。"""

    def today(self) -> date:
        return date.today()


def shift_iso(seconds: int) -> str:
    """从当前本地时间平移若干秒，用于授权过期时间。"""
    moment = datetime.now().replace(microsecond=0) + timedelta(seconds=seconds)
    return moment.isoformat()
