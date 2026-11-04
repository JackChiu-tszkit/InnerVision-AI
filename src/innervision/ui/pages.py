"""业务页面、对话和隐私操作。"""

from __future__ import annotations

from datetime import date

import streamlit as st

from innervision.container import Container
from innervision.images import image_payload
from innervision.safety import is_crisis_text, support_message
from innervision.services.sedona import STATE_LABELS
from innervision.timeutil import monday_of
from innervision.ui.board import render_board, save_order
from innervision.ui.common import render_pending, show_error, stage_pending, user_commit
from innervision.ui.extra_pages import render_backup_section, render_report, render_ritual, render_trends
from innervision.ui.voice_box import offer_voice_fields, offer_voice_note

CATEGORIES = ["健康", "关系", "事业", "财务", "成长", "创造", "贡献", "其他"]
DISCLAIMER = (
    "InnerVision AI 把记录留在这台电脑上。它帮助你整理感恩、愿景、释放练习和行动，"
    "不判断心理状况，也不保证显化或疗愈结果。你可以随时停下。如果有即时危险，请联系当地紧急服务。"
)


def render_page(page: str, container: Container) -> None:
    """按侧栏选择绘制页面。"""
    if st.session_state.get("flash"):
        st.success(st.session_state.pop("flash"))
    pages = {
        "今日": render_home,
        "每日仪式": render_ritual,
        "感恩日记": render_gratitude,
        "愿景板": render_vision,
        "释放练习": render_sedona,
        "行动教练": render_actions,
        "趋势": render_trends,
        "成长报告": render_report,
        "对话": render_chat,
        "数据与隐私": render_privacy,
    }
    pages[page](container)


def render_home(container: Container) -> None:
    """今天的入口，不替代四个具体功能。"""
    st.title("InnerVision AI")
    st.info(DISCLAIMER)
    today = date.today().isoformat()
    entry = container.gratitude.get_by_date(today)
    if entry is None:
        st.write("今天还没有感恩记录。")
    else:
        st.write("今天的三件感恩已经记在本机。")
        st.text("\n".join([entry["item_1"], entry["item_2"], entry["item_3"]]))
    tasks = container.actions.list_tasks(status="open", priority="P1")
    st.subheader("未完成的 P1")
    if not tasks:
        st.write("现在没有未完成的 P1。")
    for task in tasks:
        st.text(task["title"])
    current = container.sedona.get_open()
    st.subheader("释放练习")
    if current is None:
        st.write("没有进行中的练习。")
    else:
        label = STATE_LABELS.get(current["state"], current["state"])
        st.write(f"有一次练习停在「{label}」。可以回到释放练习页面继续、暂停或结束。")
    ritual = container.rituals.get_open()
    st.subheader("每日仪式")
    if ritual is None:
        morning = container.rituals.latest("morning", today)
        evening = container.rituals.latest("evening", today)
        if morning and morning["state"] == "completed":
            st.write("今天的晨间仪式已经完成。")
        else:
            st.write("今天的晨间仪式还没有完成。")
        if evening and evening["state"] == "completed":
            st.write("今天的晚间仪式已经完成。")
        else:
            st.write("今天的晚间仪式还没有完成。")
    else:
        ritual_label = "晨间仪式" if ritual["kind"] == "morning" else "晚间仪式"
        st.write(f"{ritual_label}还没结束。可以回到每日仪式继续、暂停或结束。")
    if container.reports.get(monday_of(date.today()).isoformat()):
        st.write("本周成长报告已经保存在本机。")
    _show_safety_banner(container)


def render_gratitude(container: Container) -> None:
    """记录三件感恩，并查看周回顾。"""
    st.title("感恩日记")
    st.caption("一天三件。确认之前不会写入数据库。")
    render_pending(container, {"gratitude.create", "gratitude.delete"})
    offer_voice_fields(container, ["g1", "g2", "g3"])
    with st.form("gratitude-form"):
        entry_date = st.date_input("日期", value=date.today())
        item_1 = st.text_input("第一件", key="g1")
        item_2 = st.text_input("第二件", key="g2")
        item_3 = st.text_input("第三件", key="g3")
        reflection = st.text_area("补充（可选）")
        submitted = st.form_submit_button("准备保存")
    if submitted:
        _stage_gratitude(container, entry_date, item_1, item_2, item_3, reflection)
    week = monday_of(date.today()).isoformat()
    review = container.gratitude.weekly_review(week)
    st.subheader("本周回顾")
    st.text(review["narrative"])
    if review["items"]:
        st.text("\n".join(f"- {item}" for item in review["items"]))
    st.subheader("最近记录")
    for entry in container.gratitude.list_recent(14):
        st.markdown(f"**{entry['entry_date']}**")
        st.text("\n".join([entry["item_1"], entry["item_2"], entry["item_3"]]))
        if st.button("准备删除这一天", key=f"delete-gratitude-{entry['id']}"):
            token = container.gratitude.propose_delete(int(entry["id"]))
            stage_pending(token, "gratitude.delete", {"日期": entry["entry_date"]}, "确认后会删除这一天的感恩记录。")
            st.rerun()


def render_vision(container: Container) -> None:
    """创建愿景卡片。图片只保存在本机数据目录。"""
    st.title("愿景板")
    st.caption("可以拖动图片卡片调整顺序。新建卡片在确认之前不会保存。")
    render_pending(container, {"vision.create", "vision.delete", "action.create", "action.link"})
    render_board(container)
    with st.form("vision-form"):
        title = st.text_input("标题")
        category = st.selectbox("分类", CATEGORIES)
        custom_category = st.text_input("自定分类（可选）")
        description = st.text_area("描述（可选）")
        use_date = st.checkbox("设定目标日期")
        target_date = st.date_input("目标日期", value=date.today())
        milestones = st.text_area("里程碑，一行一个")
        upload = st.file_uploader("本地图片（可选）", type=["png", "jpg", "jpeg", "webp", "gif"])
        submitted = st.form_submit_button("准备保存")
    if submitted:
        _stage_vision(
            container,
            title,
            custom_category.strip() or category,
            description,
            target_date.isoformat() if use_date else None,
            milestones,
            upload,
        )
    cards = container.vision.list_cards()
    tasks = container.actions.list_tasks()
    columns = st.columns(2) if cards else []
    for index, card in enumerate(cards):
        with columns[index % 2]:
            st.subheader(card["title"])
            st.caption(f"{card['category']} · {card['target_date'] or '未设日期'}")
            if card["description"]:
                st.text(card["description"])
            if len(cards) > 1:
                move_left, move_right = st.columns(2)
                ordered = [int(item["id"]) for item in cards]
                if index > 0 and move_left.button("前移", key=f"up-{card['id']}"):
                    ordered[index - 1], ordered[index] = ordered[index], ordered[index - 1]
                    save_order(container, ordered)
                if index < len(cards) - 1 and move_right.button("后移", key=f"down-{card['id']}"):
                    ordered[index + 1], ordered[index] = ordered[index], ordered[index + 1]
                    save_order(container, ordered)
            image = container.vision.image_file(card["image_name"])
            if image is not None:
                st.image(str(image))
            for milestone in card["milestones"]:
                mark = "已完成" if milestone["done"] else "未完成"
                st.text(f"{mark} · {milestone['title']}")
                if st.button(
                    "确认改为" + ("未完成" if milestone["done"] else "已完成"),
                    key=f"milestone-{milestone['id']}",
                ):
                    _commit_quietly(
                        container,
                        container.vision.propose_toggle(int(milestone["id"]), not bool(milestone["done"])),
                    )
            open_tasks = [task for task in tasks if task["status"] == "open"]
            if open_tasks:
                selected = st.selectbox(
                    "关联已有行动",
                    options=[task["id"] for task in open_tasks],
                    format_func=lambda task_id, pool=open_tasks: next(
                        task["title"] for task in pool if task["id"] == task_id
                    ),
                    key=f"link-task-{card['id']}",
                )
                if st.button("准备关联", key=f"link-{card['id']}"):
                    token = container.actions.propose_link(int(selected), int(card["id"]))
                    stage_pending(token, "action.link", {"愿景": card["title"]}, "确认后才会关联行动。")
                    st.rerun()
            for draft in container.actions.suggest_from_vision(card):
                if st.button(f"准备做成行动：{draft['title']}", key=f"draft-{card['id']}-{draft['title']}"):
                    token = container.actions.propose_create(draft)
                    stage_pending(token, "action.create", {"行动": draft["title"], "优先级": "P2"}, "确认后才会保存这项行动。")
                    st.rerun()
            if st.button("准备删除卡片", key=f"delete-vision-{card['id']}"):
                token = container.vision.propose_delete(int(card["id"]))
                stage_pending(token, "vision.delete", {"标题": card["title"]}, "确认后会删除这张卡片和它的图片。")
                st.rerun()


def render_sedona(container: Container) -> None:
    """按固定步骤练习。发送按钮就是这一步的确认。"""
    st.title("释放练习")
    st.caption("每次只有一个问题。可以暂停、跳过或结束。练习不会要求你必须放下，也不承诺结果。")
    st.caption("点击“发送并保存”即表示确认把这一步写在本机。")
    _show_safety_banner(container)
    current = container.sedona.get_open()
    if current is None:
        if st.button("开始练习", type="primary"):
            _commit_quietly(container, container.sedona.propose_start())
        return
    detail = container.sedona.get_detail(int(current["id"])) or {}
    label = STATE_LABELS.get(current["state"], current["state"])
    st.subheader(label)
    if detail.get("assistant_message"):
        st.text(detail["assistant_message"])
    if current["state"] == "paused":
        if st.button("继续", type="primary"):
            _commit_quietly(container, container.sedona.propose_resume(int(current["id"])))
    else:
        st.session_state.setdefault("sedona_input_key", 0)
        answer = st.text_area("你的回答", key=f"sedona-answer-{st.session_state['sedona_input_key']}")
        if st.button("发送并保存", type="primary"):
            _send_sedona(container, int(current["id"]), answer)
    pause, skip, end = st.columns(3)
    if pause.button("暂停"):
        _send_sedona(container, int(current["id"]), "暂停")
    if skip.button("跳过"):
        _send_sedona(container, int(current["id"]), "跳过")
    if end.button("结束"):
        _send_sedona(container, int(current["id"]), "结束")
    with st.expander("这次练习的记录"):
        for turn in detail.get("turns", []):
            speaker = "你" if turn["role"] == "user" else "引导"
            st.text(f"{speaker}：{turn['content']}")


def render_actions(container: Container) -> None:
    """管理 P1 / P2，并查看每周数量。"""
    st.title("行动教练")
    st.caption("P1 是紧急且重要，P2 是为未来积累。带“确认”的按钮会立即写入。")
    render_pending(container, {"action.create", "action.weekly_note"})
    with st.form("action-form"):
        title = st.text_input("行动")
        priority = st.selectbox("优先级", ["P1", "P2"])
        detail = st.text_area("补充（可选）")
        submitted = st.form_submit_button("准备保存")
    if submitted:
        if not title.strip():
            st.warning("请先写下行动。")
        else:
            try:
                token = container.actions.propose_create(
                    {"title": title, "priority": priority, "detail": detail, "vision_id": None}
                )
            except Exception as exc:
                show_error(exc)
            else:
                stage_pending(
                    token,
                    "action.create",
                    {"行动": title.strip(), "优先级": priority},
                    "确认后才会保存这项行动。",
                )
                st.rerun()
    week = monday_of(date.today()).isoformat()
    review = container.actions.weekly_review(week)
    st.subheader("本周回顾")
    st.text(review["narrative"])
    if review["notes"]:
        st.caption("本周备注")
        st.text(review["notes"])
    note = st.text_area("本周复盘备注", value=review["notes"])
    if st.button("准备保存备注"):
        try:
            token = container.actions.propose_weekly_note(week, note)
        except Exception as exc:
            show_error(exc)
        else:
            stage_pending(token, "action.weekly_note", {"周": week}, "确认后才会保存本周备注。")
            st.rerun()
    for task in container.actions.list_tasks():
        state = "已完成" if task["status"] == "done" else "未完成"
        st.text(f"{task['priority']} · {state} · {task['title']}")
        if task["detail"]:
            st.caption(task["detail"])
        if task["status"] == "open":
            if st.button("确认完成", key=f"done-{task['id']}"):
                _commit_quietly(container, container.actions.propose_complete(int(task["id"])))
        elif st.button("确认重新打开", key=f"reopen-{task['id']}"):
            _commit_quietly(container, container.actions.propose_reopen(int(task["id"])))


def render_chat(container: Container) -> None:
    """对话助手。它只能提出写入，确认按钮才真正保存。"""
    st.title("对话")
    st.caption("助手可以识别感恩、愿景、释放练习和行动。写入需要你再点一次确认。")
    offer_voice_note(container, "chat-voice")
    if st.session_state.get("safety_notice"):
        st.warning(support_message())
    render_pending(
        container,
        {
            "gratitude.create",
            "vision.create",
            "action.create",
            "action.complete",
            "sedona.start",
            "sedona.step",
        },
    )
    history = st.session_state.setdefault("chat_history", [])
    prompt = st.chat_input("想记录或整理什么？")
    if prompt:
        if is_crisis_text(prompt):
            current = container.sedona.get_open()
            if current is not None:
                container.sedona.stop_for_safety(int(current["id"]))
            ritual = container.rituals.get_open()
            if ritual is not None:
                container.rituals.stop_for_safety(int(ritual["id"]))
            st.session_state["safety_notice"] = True
            history.append(("user", prompt))
            history.append(("assistant", support_message()))
        else:
            reply = container.orchestrator.handle(prompt)
            history.append(("user", prompt))
            history.append(("assistant", reply.message))
            if reply.safety_stop:
                st.session_state["safety_notice"] = True
            if reply.pending_token and reply.pending_action:
                stage_pending(reply.pending_token, reply.pending_action, reply.preview or {}, reply.message)
        st.rerun()
    for role, content in history:
        with st.chat_message(role):
            st.text(content)


def render_privacy(container: Container) -> None:
    """导出或清空本机数据。清空需要输入固定句子并再次确认。"""
    st.title("数据与隐私")
    st.write("日记、练习、愿景图片和目标都在本机。导出文件也只在你的电脑上生成。")
    payload = container.privacy.export_bytes()
    st.download_button(
        "下载导出文件",
        data=payload,
        file_name="innervision-export.zip",
        mime="application/zip",
    )
    render_backup_section(container)
    render_pending(container, {"privacy.delete_all", "backup.restore"})
    st.subheader("删除全部本地数据")
    st.caption("需要输入“删除全部本地数据”，然后再点确认写入。确认后也会删除本地备份。")
    phrase = st.text_input("确认语句", key="delete-phrase")
    if st.button("准备删除全部数据"):
        try:
            token = container.privacy.propose_delete_all(phrase)
        except Exception as exc:
            show_error(exc)
        else:
            stage_pending(token, "privacy.delete_all", {"操作": "删除全部本地数据"}, "确认后会删除记录、图片、备份和日志。")
            st.rerun()


def _stage_gratitude(container: Container, entry_date, item_1: str, item_2: str, item_3: str, reflection: str) -> None:
    if not all(part.strip() for part in (item_1, item_2, item_3)):
        st.warning("请写下三件感恩的事。")
        return
    payload = {
        "entry_date": entry_date.isoformat(),
        "item_1": item_1,
        "item_2": item_2,
        "item_3": item_3,
        "reflection": reflection,
    }
    try:
        token = container.gratitude.propose_create(payload)
    except Exception as exc:
        show_error(exc)
        return
    stage_pending(
        token,
        "gratitude.create",
        {"日期": payload["entry_date"], "事项": [item_1.strip(), item_2.strip(), item_3.strip()]},
        "确认后才会保存今天的感恩日记。",
    )
    st.rerun()


def _stage_vision(container, title, category, description, target_date, milestones, upload) -> None:
    if not title.strip():
        st.warning("请先写下愿景标题。")
        return
    payload = {
        "title": title,
        "category": category,
        "description": description,
        "target_date": target_date,
        "milestones": [line.strip() for line in milestones.splitlines() if line.strip()],
        "image_base64": "",
        "image_ext": "",
    }
    preview = {"标题": title.strip(), "分类": category}
    if target_date:
        preview["目标日期"] = target_date
    if payload["milestones"]:
        preview["里程碑"] = payload["milestones"]
    if upload is not None:
        try:
            payload.update(image_payload(upload.name, upload.getvalue()))
        except Exception as exc:
            show_error(exc)
            return
        preview["图片"] = "将保存一张本地图片"
    try:
        token = container.vision.propose_create(payload)
    except Exception as exc:
        show_error(exc)
        return
    stage_pending(token, "vision.create", preview, "确认后才会保存这张愿景卡片。")
    st.rerun()


def _send_sedona(container: Container, session_id: int, answer: str) -> None:
    text = answer.strip()
    if not text:
        st.warning("可以写一句，也可以使用暂停、跳过或结束。")
        return
    if is_crisis_text(text):
        container.sedona.stop_for_safety(session_id)
        st.session_state["safety_notice"] = True
        st.rerun()
        return
    try:
        token = container.sedona.propose_step(session_id, text)
        user_commit(container, token)
    except Exception as exc:
        show_error(exc)
        return
    st.session_state["sedona_input_key"] = st.session_state.get("sedona_input_key", 0) + 1
    st.rerun()


def _commit_quietly(container: Container, token: str) -> None:
    try:
        user_commit(container, token)
    except Exception as exc:
        show_error(exc)
    else:
        st.session_state["flash"] = "已保存在本机。"
        st.rerun()


def _show_safety_banner(container: Container) -> None:
    if st.session_state.get("safety_notice"):
        st.warning(support_message())
        return
    summaries = container.sedona.list_summaries(1)
    sedona_stopped = bool(summaries and summaries[0]["safety_stopped"] and container.sedona.get_open() is None)
    ritual_stopped = False
    today = date.today().isoformat()
    for kind in ("morning", "evening"):
        item = container.rituals.latest(kind, today)
        if item and item["safety_stopped"] and container.rituals.get_open() is None:
            ritual_stopped = True
    if sedona_stopped or ritual_stopped:
        st.warning(support_message())
