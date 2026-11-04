"""可选录音入口。未安装本地转写时只显示说明。"""

from __future__ import annotations

from pathlib import Path

import streamlit as st

from innervision.container import Container
from innervision.ui.common import show_error

_CAPTION = "本机语音是可选项。安装 faster-whisper 后，录音只在这台电脑上转写，不会上传。"


def offer_voice(container: Container, target_key: str) -> None:
    """把转写结果放进指定输入框。没有引擎时不出现录音控件。"""
    if not container.voice.available():
        st.caption(_CAPTION)
        return
    audio_input = getattr(st, "audio_input", None)
    if audio_input is None:
        st.caption("当前界面没有录音控件。")
        return
    audio = audio_input("对本机说话", key=f"mic-{target_key}")
    if audio is None or not st.button("转写到输入框", key=f"mic-btn-{target_key}"):
        return
    try:
        text = container.voice.transcribe(audio.getvalue(), Path(getattr(audio, "name", ".wav")).suffix)
    except Exception as exc:
        show_error(exc)
        return
    st.session_state[target_key] = text


def offer_voice_fields(container: Container, keys: list[str]) -> None:
    """把转写结果填进第一个还空着的输入框。"""
    if not container.voice.available():
        st.caption(_CAPTION)
        return
    audio_input = getattr(st, "audio_input", None)
    if audio_input is None:
        st.caption("当前界面没有录音控件。")
        return
    audio = audio_input("对本机说话", key="mic-fields")
    if audio is None or not st.button("转写到第一条空白", key="mic-fields-btn"):
        return
    try:
        text = container.voice.transcribe(audio.getvalue(), Path(getattr(audio, "name", ".wav")).suffix)
    except Exception as exc:
        show_error(exc)
        return
    for key in keys:
        if not str(st.session_state.get(key, "")).strip():
            st.session_state[key] = text
            return
    if keys:
        st.session_state[keys[0]] = text


def offer_voice_note(container: Container, state_key: str) -> None:
    """显示可复制的转写结果，不自动送出。"""
    if not container.voice.available():
        st.caption(_CAPTION)
        return
    audio_input = getattr(st, "audio_input", None)
    if audio_input is None:
        st.caption("当前界面没有录音控件。")
        return
    audio = audio_input("对本机说话", key=f"mic-{state_key}")
    if audio is not None and st.button("转写", key=f"mic-btn-{state_key}"):
        try:
            st.session_state[state_key] = container.voice.transcribe(
                audio.getvalue(),
                Path(getattr(audio, "name", ".wav")).suffix,
            )
        except Exception as exc:
            show_error(exc)
    if st.session_state.get(state_key):
        st.caption("转写结果可以复制到输入框。音频没有上传。")
        st.text(st.session_state[state_key])
