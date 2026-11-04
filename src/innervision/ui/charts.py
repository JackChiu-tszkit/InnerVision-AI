"""用 Plotly 画数量趋势。图上只有周次和数字。"""

from __future__ import annotations

import plotly.graph_objects as go

_GREEN = "#6f8f7b"
_SAND = "#c4a484"
_INK = "#2c3330"


def gratitude_figure(series: dict) -> go.Figure:
    """每周记下感恩的天数。"""
    figure = go.Figure(
        go.Bar(x=series["weeks"], y=series["gratitude_days"], marker_color=_GREEN, name="感恩天数")
    )
    return _layout(figure, "每周感恩天数")


def action_figure(series: dict) -> go.Figure:
    """每周完成的 P1 和 P2。"""
    figure = go.Figure()
    figure.add_bar(x=series["weeks"], y=series["p1_done"], name="P1", marker_color=_GREEN)
    figure.add_bar(x=series["weeks"], y=series["p2_done"], name="P2", marker_color=_SAND)
    figure.update_layout(barmode="group")
    return _layout(figure, "每周完成的行动")


def emotion_figure(series: dict) -> go.Figure:
    """释放练习前后的平均强度。没有记录的周留空。"""
    figure = go.Figure()
    figure.add_scatter(
        x=series["weeks"],
        y=series["sedona_before"],
        name="练习前",
        mode="lines+markers",
        line={"color": _SAND},
    )
    figure.add_scatter(
        x=series["weeks"],
        y=series["sedona_after"],
        name="练习后",
        mode="lines+markers",
        line={"color": _GREEN},
    )
    return _layout(figure, "释放练习的强度")


def _layout(figure: go.Figure, title: str) -> go.Figure:
    figure.update_layout(
        title={"text": title, "font": {"color": _INK, "size": 16}},
        margin={"l": 24, "r": 16, "t": 48, "b": 32},
        height=320,
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font={"color": _INK},
        legend={"orientation": "h"},
    )
    figure.update_xaxes(type="category")
    return figure
