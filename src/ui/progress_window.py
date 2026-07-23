"""
Окно прогресса создания аудиокниги.
Отображает этап, объём/текущую главу, полосу прогресса и оценку времени.
"""

from __future__ import annotations

import logging
import threading
import time
import tkinter
from typing import Callable, Optional

import customtkinter as ctk

from src.utils.scope_display import (
    STAGE_PREPARE,
    STAGE_SYNTH,
    STAGE_CHAPTER_MERGE,
    STAGE_BOOK_MERGE,
    STAGE_DONE,
    STAGE_LABELS,
)

logger = logging.getLogger(__name__)


class ProgressWindow(ctk.CTkToplevel):
    """Окно прогресса создания аудиокниги.

    Отображает:
    - Текущий этап (синтез / склейка глав / склейка книги)
    - Текущую главу, режим объёма и всего глав
    - Полосу прогресса и оценку времени
    - Кнопки паузы и отмены
    """

    def __init__(
        self,
        parent: ctk.CTk,
        title: str = "Создание аудиокниги...",
    ):
        super().__init__(parent)
        self.title(title)
        self.geometry("520x340")
        self.resizable(False, False)

        # Центрируем относительно родителя
        self.transient(parent)

        # Дожидаемся отображения окна перед grab_set,
        # иначе может упасть с TclError: grab failed: window not viewable
        self.wait_visibility()
        self.grab_set()

        # Состояние
        self._start_time = time.time()
        self._paused = False
        self._canceled = False
        self._pause_callback: Optional[Callable] = None
        self._resume_callback: Optional[Callable] = None
        self._cancel_callback: Optional[Callable] = None

        # Детали синтеза
        self._current_text = ""
        self._current_voice = ""
        self._current_engine = ""

        # Защита от закрытия
        self.protocol("WM_DELETE_WINDOW", self._on_cancel)

        self._create_widgets()

    def _create_widgets(self):
        """Создание виджетов окна."""
        # Основной контейнер
        main_frame = ctk.CTkFrame(self)
        main_frame.pack(fill="both", expand=True, padx=20, pady=20)

        # Заголовок
        self.title_label = ctk.CTkLabel(
            main_frame,
            text="Создание аудиокниги",
            font=ctk.CTkFont(size=18, weight="bold"),
        )
        self.title_label.pack(pady=(0, 10))

        # Этап вместо процента
        self.stage_label = ctk.CTkLabel(
            main_frame,
            text=STAGE_LABELS[STAGE_PREPARE],
            font=ctk.CTkFont(size=15, weight="bold"),
        )
        self.stage_label.pack(pady=(0, 6))

        # Глава / объём / всего
        self.scope_label = ctk.CTkLabel(
            main_frame,
            text="",
            font=ctk.CTkFont(size=12),
            text_color="gray",
            wraplength=470,
        )
        self.scope_label.pack(pady=(0, 8))

        # Статус (детали: сегменты и т.п.)
        self.status_label = ctk.CTkLabel(
            main_frame,
            text="Подготовка...",
            font=ctk.CTkFont(size=12),
            wraplength=470,
        )
        self.status_label.pack(pady=(0, 10))

        # Полоса прогресса
        self.progress_bar = ctk.CTkProgressBar(main_frame, width=420)
        self.progress_bar.pack(pady=(0, 8))
        self.progress_bar.set(0.0)

        # Оценка времени
        self.time_label = ctk.CTkLabel(
            main_frame,
            text="",
            font=ctk.CTkFont(size=12),
            text_color="gray",
        )
        self.time_label.pack(pady=(0, 10))

        # Детальная информация о синтезе (текст сегмента)
        self.detail_frame = ctk.CTkFrame(main_frame)
        self.detail_frame.pack(fill="x", padx=10, pady=(0, 10))

        self.text_preview_label = ctk.CTkLabel(
            self.detail_frame,
            text="",
            font=ctk.CTkFont(size=11),
            wraplength=470,
            justify="left",
            anchor="w",
            text_color="gray",
        )
        self.text_preview_label.pack(fill="x", padx=10, pady=(5, 2))

        self.voice_engine_label = ctk.CTkLabel(
            self.detail_frame,
            text="",
            font=ctk.CTkFont(size=11),
            anchor="w",
            text_color="gray",
        )
        self.voice_engine_label.pack(fill="x", padx=10, pady=(0, 5))

        # По умолчанию скрываем детали
        self.detail_frame.pack_forget()

        # Кнопки
        btn_frame = ctk.CTkFrame(main_frame)
        btn_frame.pack(fill="x", pady=(5, 0))

        self.pause_btn = ctk.CTkButton(
            btn_frame,
            text="⏸ Пауза",
            command=self._on_pause,
            width=120,
        )
        self.pause_btn.pack(side="left", padx=10)

        self.cancel_btn = ctk.CTkButton(
            btn_frame,
            text="✕ Отмена",
            command=self._on_cancel,
            width=120,
            fg_color="red",
            hover_color="darkred",
        )
        self.cancel_btn.pack(side="right", padx=10)

        # Кнопка закрытия (скрыта по умолчанию, показывается при ошибке/завершении)
        self.close_btn = ctk.CTkButton(
            main_frame,
            text="✕ Закрыть",
            command=self.close,
            width=120,
        )
        # Не показываем её сразу — только когда процесс завершён

    def show_close_button(self):
        """Показать кнопку закрытия (при ошибке или завершении).

        Внимание: этот метод должен вызываться из главного потока tkinter.
        Для вызова из других потоков используйте safe_show_close_button().
        """
        self.close_btn.pack(pady=(10, 0))
        self.pause_btn.pack_forget()
        self.cancel_btn.pack_forget()

    def safe_show_close_button(self):
        """Безопасный вызов show_close_button из любого потока.

        Использует after(0, ...) для отправки вызова в главный поток tkinter.
        """
        self.after(0, self.show_close_button)

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
        """Обновление прогресса.

        Args:
            status: Текстовый статус.
            progress: Прогресс от 0.0 до 1.0 (для полосы и ETA).
            current_text: Текст текущего синтезируемого сегмента.
            voice: Имя голоса.
            engine: Название TTS-движка.
            segment_index: Номер текущего сегмента.
            segment_total: Всего сегментов.
            stage: Код этапа (synth / chapter_merge / book_merge …).
            scope_line: Строка «Глава N · режим · всего глав X».
        """
        # Обновление в главном потоке
        self.after(
            0,
            self._do_update_progress,
            status,
            progress,
            current_text,
            voice,
            engine,
            segment_index,
            segment_total,
            stage,
            scope_line,
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
        """Обновление виджетов прогресса (в главном потоке)."""
        try:
            if stage:
                self.stage_label.configure(
                    text=STAGE_LABELS.get(stage, stage),
                )
            if scope_line is not None:
                self.scope_label.configure(text=scope_line)

            self.status_label.configure(text=status)
            self.progress_bar.set(max(0.0, min(1.0, progress)))

            # Оценка времени
            elapsed = time.time() - self._start_time
            if progress > 0.01:
                estimated_total = elapsed / progress
                remaining = estimated_total - elapsed
                if remaining > 0:
                    self.time_label.configure(
                        text=f"Прошло: {self._format_time(elapsed)} | "
                             f"Осталось: {self._format_time(remaining)}"
                    )
                else:
                    self.time_label.configure(
                        text=f"Прошло: {self._format_time(elapsed)}"
                    )
            else:
                self.time_label.configure(
                    text=f"Прошло: {self._format_time(elapsed)}"
                )

            # Детальная информация о синтезе
            if current_text is not None:
                # Показываем детальную панель
                self.detail_frame.pack(fill="x", padx=10, pady=(0, 10))

                preview = current_text[:120]
                self.text_preview_label.configure(
                    text=f"📖 {preview}{'…' if len(current_text) > 120 else ''}"
                )

                parts = []
                if engine:
                    engine_name = {
                        "edge": "Edge TTS",
                        "piper": "Piper (локальный)",
                        "silero": "Silero TTS",
                        "supertonic": "Supertonic 3",
                    }.get(engine, engine)
                    parts.append(f"⚙ {engine_name}")
                if voice:
                    # Краткое имя голоса (убираем префикс языка)
                    short_voice = voice.split("-", 1)[-1] if "-" in voice else voice
                    parts.append(f"🗣 {short_voice}")
                if segment_index is not None and segment_total is not None:
                    parts.append(f"#{segment_index}/{segment_total}")

                self.voice_engine_label.configure(text=" | ".join(parts))
            else:
                # Если нет деталей — скрываем панель (для этапов без синтеза)
                self.detail_frame.pack_forget()

        except (AttributeError, tkinter.TclError):
            # Окно могло быть уничтожено (закрыто/отмена) — игнорируем
            pass

    def set_pause_callback(self, callback: Callable):
        """Установка колбэка для паузы."""
        self._pause_callback = callback

    def set_resume_callback(self, callback: Callable):
        """Установка колбэка для возобновления."""
        self._resume_callback = callback

    def set_cancel_callback(self, callback: Callable):
        """Установка колбэка для отмены."""
        self._cancel_callback = callback

    def _on_pause(self):
        """Обработчик паузы."""
        if self._paused:
            self._paused = False
            self.pause_btn.configure(text="⏸ Пауза")
            if self._resume_callback:
                self._resume_callback()
        else:
            self._paused = True
            self.pause_btn.configure(text="▶ Продолжить")
            if self._pause_callback:
                self._pause_callback()

    def _on_cancel(self):
        """Обработчик отмены."""
        self._canceled = True
        if self._cancel_callback:
            self._cancel_callback()
        self.destroy()

    def is_canceled(self) -> bool:
        """Проверка, была ли нажата отмена."""
        return self._canceled

    def close(self):
        """Закрытие окна прогресса."""
        self.after(0, self.destroy)

    @staticmethod
    def _format_time(seconds: float) -> str:
        """Форматирование времени в ЧЧ:ММ:СС."""
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        secs = int(seconds % 60)
        if hours > 0:
            return f"{hours:02d}:{minutes:02d}:{secs:02d}"
        return f"{minutes:02d}:{secs:02d}"
