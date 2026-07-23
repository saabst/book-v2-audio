"""
Шаг 8: Создание аудиокниги — прогресс внутри визарда (не модальное окно).
"""

from __future__ import annotations

import logging
import time
import tkinter
from dataclasses import dataclass
from typing import Callable, Optional

import customtkinter as ctk

from src.config.settings import Settings
from src.utils.scope_display import (
    STAGE_PREPARE,
    STAGE_SYNTH,
    STAGE_CHAPTER_MERGE,
    STAGE_BOOK_MERGE,
    STAGE_DONE,
    STAGE_LABELS,
)

logger = logging.getLogger(__name__)

CREATE_TEXTS = {
    "ru": {
        "title": "Создание аудиокниги",
        "synth_block": "Синтез речи по главам",
        "merge_block": "Склеивание по главам",
        "waiting": "Ожидание…",
        "pause": "⏸ Пауза",
        "resume": "▶ Продолжить",
        "cancel": "✕ Отмена",
    },
    "en": {
        "title": "Creating audiobook",
        "synth_block": "Speech synthesis by chapters",
        "merge_block": "Merging by chapters",
        "waiting": "Waiting…",
        "pause": "⏸ Pause",
        "resume": "▶ Resume",
        "cancel": "✕ Cancel",
    },
    "ja": {
        "title": "オーディオブック作成",
        "synth_block": "章ごとの音声合成",
        "merge_block": "章ごとの結合",
        "waiting": "待機中…",
        "pause": "⏸ 一時停止",
        "resume": "▶ 再開",
        "cancel": "✕ キャンセル",
    },
    "zh": {
        "title": "正在创建有声书",
        "synth_block": "按章语音合成",
        "merge_block": "按章合并",
        "waiting": "等待中…",
        "pause": "⏸ 暂停",
        "resume": "▶ 继续",
        "cancel": "✕ 取消",
    },
}


@dataclass
class _StageBlock:
    """Виджеты одного этапа (синтез / склейка)."""
    frame: ctk.CTkFrame
    title_label: ctk.CTkLabel
    scope_label: ctk.CTkLabel
    status_label: ctk.CTkLabel
    progress_bar: ctk.CTkProgressBar
    time_label: ctk.CTkLabel
    detail_frame: Optional[ctk.CTkFrame] = None
    text_preview_label: Optional[ctk.CTkLabel] = None
    voice_engine_label: Optional[ctk.CTkLabel] = None
    eta_start_time: Optional[float] = None
    last_remaining_txt: str = ""


class PageCreate(ctk.CTkFrame):
    """Встроенная страница прогресса создания (шаг визарда)."""

    def __init__(
        self,
        parent: ctk.CTkFrame,
        settings: Settings,
        on_complete: Optional[Callable] = None,
    ):
        super().__init__(parent)
        self.settings = settings
        self.on_complete = on_complete  # вызывается при показе — старт pipeline
        self._start_time = time.time()
        self._paused = False
        self._finished = False
        self._pause_callback: Optional[Callable] = None
        self._resume_callback: Optional[Callable] = None
        self._cancel_callback: Optional[Callable] = None
        self._nav_restore_callback: Optional[Callable] = None
        self._active_block: Optional[_StageBlock] = None

        self._create_widgets()
        # Автозапуск после отрисовки
        self.after(50, self._auto_start)

    def _t(self) -> dict:
        return CREATE_TEXTS.get(self.settings.ui_lang, CREATE_TEXTS["ru"])

    def _make_stage_block(
        self,
        parent: ctk.CTkFrame,
        title: str,
        *,
        with_detail: bool = False,
    ) -> _StageBlock:
        frame = ctk.CTkFrame(parent)
        frame.pack(fill="x", padx=20, pady=(0, 12))

        title_label = ctk.CTkLabel(
            frame, text=title, font=ctk.CTkFont(size=15, weight="bold"),
        )
        title_label.pack(pady=(12, 6))

        scope_label = ctk.CTkLabel(
            frame, text="", font=ctk.CTkFont(size=12), text_color="gray", wraplength=520,
        )
        scope_label.pack(pady=(0, 6))

        status_label = ctk.CTkLabel(
            frame, text=self._t()["waiting"], font=ctk.CTkFont(size=12), wraplength=520,
        )
        status_label.pack(pady=(0, 8))

        progress_bar = ctk.CTkProgressBar(frame, width=440)
        progress_bar.pack(pady=(0, 8))
        progress_bar.set(0.0)

        time_label = ctk.CTkLabel(
            frame, text="", font=ctk.CTkFont(size=12), text_color="gray",
        )
        time_label.pack(pady=(0, 10))

        detail_frame = None
        text_preview_label = None
        voice_engine_label = None
        if with_detail:
            detail_frame = ctk.CTkFrame(frame)
            text_preview_label = ctk.CTkLabel(
                detail_frame, text="", font=ctk.CTkFont(size=11),
                wraplength=500, justify="left", anchor="w", text_color="gray",
            )
            text_preview_label.pack(fill="x", padx=10, pady=(5, 2))
            voice_engine_label = ctk.CTkLabel(
                detail_frame, text="", font=ctk.CTkFont(size=11),
                anchor="w", text_color="gray",
            )
            voice_engine_label.pack(fill="x", padx=10, pady=(0, 5))

        return _StageBlock(
            frame=frame,
            title_label=title_label,
            scope_label=scope_label,
            status_label=status_label,
            progress_bar=progress_bar,
            time_label=time_label,
            detail_frame=detail_frame,
            text_preview_label=text_preview_label,
            voice_engine_label=voice_engine_label,
        )

    def _create_widgets(self):
        t = self._t()

        # Заголовок страницы — как на других шагах визарда
        self.title_label = ctk.CTkLabel(
            self, text=t["title"], font=ctk.CTkFont(size=20, weight="bold"),
        )
        self.title_label.pack(pady=(30, 16))

        self.synth_block = self._make_stage_block(
            self, t["synth_block"], with_detail=True,
        )
        self.merge_block = self._make_stage_block(
            self, t["merge_block"], with_detail=False,
        )
        self._active_block = self.synth_block
        self.synth_block.status_label.configure(text=STAGE_LABELS[STAGE_PREPARE])

        # Пауза / Отмена живут в нижней панели визарда
        self.pause_btn = None
        self.cancel_btn = None

    def _auto_start(self):
        if self.on_complete and not self._finished:
            self.on_complete()

    def set_pause_callback(self, cb: Callable):
        self._pause_callback = cb

    def set_resume_callback(self, cb: Callable):
        self._resume_callback = cb

    def set_cancel_callback(self, cb: Callable):
        self._cancel_callback = cb

    def set_nav_restore_callback(self, cb: Callable):
        """Включить стандартную навигацию «Назад» после завершения/отмены."""
        self._nav_restore_callback = cb

    def bind_nav_controls(self, pause_btn, cancel_btn):
        """Привязать кнопки нижней панели визарда."""
        self.pause_btn = pause_btn
        self.cancel_btn = cancel_btn
        pause_btn.configure(command=self._on_pause)
        cancel_btn.configure(command=self._on_cancel)

    def _block_for_stage(self, stage: Optional[str]) -> _StageBlock:
        if stage in (STAGE_CHAPTER_MERGE, STAGE_BOOK_MERGE, STAGE_DONE):
            return self.merge_block
        return self.synth_block

    def _stage_progress(self, stage: Optional[str], overall: float) -> float:
        """Локальный прогресс 0..1 внутри блока этапа."""
        w_prep = 0.08
        w_synth = 0.72
        w_ch = 0.15
        w_book = 0.05
        if stage in (None, STAGE_PREPARE):
            return max(0.0, min(1.0, overall / max(w_prep, 1e-6)))
        if stage == STAGE_SYNTH:
            return max(0.0, min(1.0, (overall - w_prep) / max(w_synth, 1e-6)))
        if stage == STAGE_CHAPTER_MERGE:
            return max(0.0, min(1.0, (overall - w_prep - w_synth) / max(w_ch, 1e-6)))
        if stage == STAGE_BOOK_MERGE:
            return max(0.0, min(1.0, (overall - w_prep - w_synth - w_ch) / max(w_book, 1e-6)))
        if stage == STAGE_DONE:
            return 1.0
        return max(0.0, min(1.0, overall))

    def update_progress(
        self,
        status: str,
        progress: float,
        current_text: Optional[str] = None,
        voice: Optional[str] = None,
        engine: Optional[str] = None,
        segment_index: Optional[int] = None,
        segment_total: Optional[int] = None,
        stage: Optional[str] = None,
        scope_line: Optional[str] = None,
    ):
        self.after(
            0, self._do_update_progress, status, progress,
            current_text, voice, engine, segment_index, segment_total,
            stage, scope_line,
        )

    def _do_update_progress(
        self,
        status: str,
        progress: float,
        current_text: Optional[str] = None,
        voice: Optional[str] = None,
        engine: Optional[str] = None,
        segment_index: Optional[int] = None,
        segment_total: Optional[int] = None,
        stage: Optional[str] = None,
        scope_line: Optional[str] = None,
    ):
        try:
            block = self._block_for_stage(stage)
            self._active_block = block

            # При переходе на склейку — синтез на 100%
            if (
                block is self.merge_block
                and stage in (STAGE_CHAPTER_MERGE, STAGE_BOOK_MERGE, STAGE_DONE)
            ):
                self.synth_block.progress_bar.set(1.0)

            if stage == STAGE_BOOK_MERGE:
                block.title_label.configure(text=STAGE_LABELS[STAGE_BOOK_MERGE])
            elif stage == STAGE_DONE:
                block.title_label.configure(text=STAGE_LABELS[STAGE_DONE])
            elif block is self.merge_block and stage == STAGE_CHAPTER_MERGE:
                block.title_label.configure(text=self._t()["merge_block"])

            if scope_line is not None:
                block.scope_label.configure(text=scope_line)
            block.status_label.configure(text=status)
            block.progress_bar.set(self._stage_progress(stage, progress))

            # ETA: свой таймер на блок, формула после каждого завершённого сегмента
            eta_ok = stage in (STAGE_SYNTH, STAGE_CHAPTER_MERGE)
            if eta_ok:
                if block.eta_start_time is None:
                    block.eta_start_time = time.time()
                    block.last_remaining_txt = ""
            elif stage in (STAGE_BOOK_MERGE, STAGE_DONE, STAGE_PREPARE):
                # для book_merge оставляем elapsed блока склейки без пересчёта сегментов
                pass

            remaining_txt = ""
            if (
                eta_ok
                and block.eta_start_time is not None
                and current_text is None
                and segment_index is not None
                and segment_total is not None
                and segment_index > 0
            ):
                stage_elapsed = time.time() - block.eta_start_time
                left = max(0, segment_total - segment_index)
                if left == 0:
                    remaining_txt = f" | Осталось: {self._fmt(0)}"
                else:
                    remaining = (stage_elapsed / segment_index) * left
                    remaining_txt = f" | Осталось: {self._fmt(remaining)}"
            elif eta_ok and block.last_remaining_txt:
                remaining_txt = block.last_remaining_txt

            if remaining_txt:
                block.last_remaining_txt = remaining_txt

            if block.eta_start_time is not None:
                block_elapsed = time.time() - block.eta_start_time
                block.time_label.configure(
                    text=f"Прошло: {self._fmt(block_elapsed)}{remaining_txt}"
                )
            elif progress > 0:
                # подготовка / финал без сегментного таймера — общее время страницы
                elapsed = time.time() - self._start_time
                block.time_label.configure(text=f"Прошло: {self._fmt(elapsed)}")

            if block.detail_frame is not None:
                if current_text is not None:
                    block.detail_frame.pack(fill="x", padx=10, pady=(0, 10))
                    preview = current_text[:120]
                    block.text_preview_label.configure(
                        text=f"📖 {preview}{'…' if len(current_text) > 120 else ''}"
                    )
                    eng_map = {
                        "edge": "Edge TTS", "piper": "Piper",
                        "silero": "Silero", "supertonic": "Supertonic",
                    }
                    parts = []
                    if engine:
                        parts.append(f"⚙ {eng_map.get(engine, engine)}")
                    if voice:
                        short = voice.split("-", 1)[-1] if "-" in voice else voice
                        parts.append(f"🗣 {short}")
                    if segment_index is not None and segment_total is not None:
                        parts.append(f"#{segment_index}/{segment_total}")
                    block.voice_engine_label.configure(text=" | ".join(parts))
                else:
                    block.detail_frame.pack_forget()
        except (AttributeError, tkinter.TclError):
            pass

    def show_finished(self, ok: bool = True):
        """После завершения/отмены: убрать Pause/Cancel, вернуть «Назад» визарда."""
        self._finished = True
        if self._nav_restore_callback:
            self._nav_restore_callback()

    def set_canceling(self):
        """После нажатия «Отмена»: кнопка неактивна, ждём конец главы и склейку."""
        if self.cancel_btn is not None:
            self.cancel_btn.configure(state="disabled")
        try:
            block = self._active_block or self.synth_block
            block.status_label.configure(text="Отмена после текущей главы…")
        except (AttributeError, tkinter.TclError):
            pass

    def _on_pause(self):
        t = self._t()
        if self._paused:
            self._paused = False
            if self.pause_btn is not None:
                self.pause_btn.configure(text=t["pause"])
            if self._resume_callback:
                self._resume_callback()
        else:
            self._paused = True
            if self.pause_btn is not None:
                self.pause_btn.configure(text=t["resume"])
            if self._pause_callback:
                self._pause_callback()

    def _on_cancel(self):
        if self._cancel_callback:
            self._cancel_callback()

    def get_data(self) -> dict:
        return {}

    def validate(self) -> bool:
        return True

    @staticmethod
    def _fmt(seconds: float) -> str:
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        secs = int(seconds % 60)
        if hours > 0:
            return f"{hours:02d}:{minutes:02d}:{secs:02d}"
        return f"{minutes:02d}:{secs:02d}"
