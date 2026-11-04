"""趋势、成长报告、每日仪式和本地备份。"""

from __future__ import annotations

from datetime import date

import streamlit as st

from innervision.container import Container
from innervision.safety import is_crisis_text, support_message
from innervision.services.ritual import STATE_LABELS
from innervision.timeutil import monday_of
from innervision.ui.charts import action_figure, emotion_figure, gratitude_figure
from innervision.ui.common import show_error, stage_pending, user_commit
from innervision.ui.voice_box import offer_voice

_KIND_LABELS = {"morning": "晨间仪式", "evening": "晚间仪式"}


def render_trends(container: Container) -> None:
    """展示最近几周的数量和强度。"""
    st.title("趋势")
    st.caption("这些图只统计数量和练习前后的强度，不显示日记或感受正文。")
    series = container.stats.series(today=date.today())
    st.plotly_chart(gratitude_figure(series), width="stretch", key="trend-gratitude")
    st.plotly_chart(action_figure(series), width="stretch", key="trend-actions")
    st.plotly_chart(emotion_figure(series), width="stretch", key="trend-emotion")


def render_report(container: Container) -> None:
    """生成本周数量回顾。看到预览后，确认才会保存。"""
    st.title("成长报告")
    st.caption("报告只根据数量生成。确认保存后才会写入本机。它不诊断，也不承诺结果。")
    week = monday_of(date.today()).isoformat()
    summary = container.reports.summary(week)
    st.text(
        "\n".join(
            [
                f"{summary['week_start']} 到 {summary['week_end']}",
                f"感恩 {summary['gratitude_days']} 天",
                f"完成 P1 {summary['p1_done']} 项，P2 {summary['p2_done']} 项",
                f"释放练习 {summary['sedona_sessions']} 次",
                f"晨间仪式 {summary['ritual_morning']} 次，晚间仪式 {summary['ritual_evening']} 次",
            ]
        )
    )
    saved = container.reports.get(week)
    if saved:
        st.subheader("已保存的报告")
        st.text(saved["body"])
        source = "本地模型" if saved["source"] == "local_model" else "本机模板"
        st.caption(f"来源：{source}")
    if st.button("生成预览"):
        try:
            body, source = container.reports.compose(week)
        except Exception as exc:
            show_error(exc)
        else:
            st.session_state["report_preview"] = {"week": week, "body": body, "source": source}
    preview = st.session_state.get("report_preview")
    if preview and preview.get("week") == week:
        st.subheader("预览")
        st.text(preview["body"])
        if st.button("确认保存这份报告", type="primary"):
            try:
                token = container.reports.propose_save(week, preview["body"], preview["source"])
                user_commit(container, token)
            except Exception as exc:
                show_error(exc)
            else:
                st.session_state.pop("report_preview", None)
                st.session_state["flash"] = "成长报告已保存在本机。"
                st.rerun()


def render_ritual(container: Container) -> None:
    """晨间或晚间的固定问题。发送按钮就是这一步的确认。"""
    st.title("每日仪式")
    st.caption("晨间是一句提醒、一件小事和今天要照顾的一件事。晚间是记下今天，以及此刻的轻松程度。")
    st.caption("每次只有一个问题。可以暂停、跳过或结束。点击发送或开始，就会把这一步记在本机。")
    if st.session_state.get("safety_notice"):
        st.warning(support_message())
    current = container.rituals.get_open()
    if current is None:
        _ritual_idle(container)
        return
    detail = container.rituals.get_detail(int(current["id"])) or {}
    kind = _KIND_LABELS.get(current["kind"], current["kind"])
    label = STATE_LABELS.get(current["state"], current["state"])
    st.subheader(f"{kind} · {label}")
    if detail.get("assistant_message"):
        st.text(detail["assistant_message"])
    if current["state"] == "paused":
        if st.button("继续", type="primary"):
            _commit_ritual(container, container.rituals.propose_resume(int(current["id"])))
    else:
        st.session_state.setdefault("ritual_input_key", 0)
        answer_key = f"ritual-answer-{st.session_state['ritual_input_key']}"
        offer_voice(container, answer_key)
        answer = st.text_area("你的回答", key=answer_key)
        if st.button("发送并保存", type="primary"):
            _send_ritual(container, int(current["id"]), answer)
    pause, skip, end = st.columns(3)
    if pause.button("暂停"):
        _send_ritual(container, int(current["id"]), "暂停")
    if skip.button("跳过"):
        _send_ritual(container, int(current["id"]), "跳过")
    if end.button("结束"):
        _send_ritual(container, int(current["id"]), "结束")
    with st.expander("这次仪式的记录"):
        for turn in detail.get("turns", []):
            speaker = "你" if turn["role"] == "user" else "引导"
            st.text(f"{speaker}：{turn['content']}")


def render_backup_section(container: Container) -> None:
    """创建或恢复本地备份。恢复需要固定语句和再次确认。"""
    st.subheader("本地备份")
    st.caption("备份写在本机数据目录。恢复会覆盖当前记录和图片，需要输入“恢复本地备份”，然后再点确认写入。")
    if st.button("创建本地备份"):
        try:
            path = container.backup.create()
        except Exception as exc:
            show_error(exc)
        else:
            st.session_state["flash"] = f"备份已写在本机：{path.name}"
            st.rerun()
    backups = container.backup.list_backups()
    if not backups:
        st.write("还没有本地备份。")
        return
    for item in backups:
        st.text(f"{item['name']} · {item['size']} 字节")
    selected = st.selectbox(
        "要恢复的备份",
        options=[item["name"] for item in backups],
    )
    phrase = st.text_input("恢复确认语句", key="backup-phrase")
    if st.button("准备恢复"):
        try:
            token = container.backup.propose_restore(selected, phrase)
        except Exception as exc:
            show_error(exc)
        else:
            stage_pending(
                token,
                "backup.restore",
                {"备份": selected},
                "确认后会用这份备份覆盖当前的记录和图片。",
            )
            st.rerun()


def _ritual_idle(container: Container) -> None:
    today = date.today().isoformat()
    for kind, label in _KIND_LABELS.items():
        latest = container.rituals.latest(kind, today)
        if latest and latest["state"] == "completed":
            st.write(f"今天的{label}已经完成。")
        elif latest and latest["safety_stopped"]:
            st.warning(support_message())
        else:
            st.write(f"今天的{label}还没有完成。")
    morning, evening = st.columns(2)
    if morning.button("开始晨间仪式", type="primary"):
        _commit_ritual(container, container.rituals.propose_start("morning"))
    if evening.button("开始晚间仪式"):
        _commit_ritual(container, container.rituals.propose_start("evening"))


def _send_ritual(container: Container, session_id: int, answer: str) -> None:
    text = answer.strip()
    if not text:
        st.warning("可以写一句，也可以使用暂停、跳过或结束。")
        return
    if is_crisis_text(text):
        container.rituals.stop_for_safety(session_id)
        sedona = container.sedona.get_open()
        if sedona is not None:
            container.sedona.stop_for_safety(int(sedona["id"]))
        st.session_state["safety_notice"] = True
        st.rerun()
        return
    try:
        token = container.rituals.propose_step(session_id, text)
        user_commit(container, token)
    except Exception as exc:
        show_error(exc)
        return
    st.session_state["ritual_input_key"] = st.session_state.get("ritual_input_key", 0) + 1
    st.rerun()


def _commit_ritual(container: Container, token: str) -> None:
    try:
        user_commit(container, token)
    except Exception as exc:
        show_error(exc)
    else:
        st.session_state["flash"] = "已保存在本机。"
        st.rerun()
