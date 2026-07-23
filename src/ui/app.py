"""
Главное окно приложения на CustomTkinter.
Управляет пошаговым мастером настройки и запуском pipeline.
"""

from __future__ import annotations

import asyncio
import logging
import queue
import threading
import traceback
from pathlib import Path
from typing import Optional

import customtkinter as ctk

from src.config.settings import Settings, load_settings, save_settings
from src.config.key_manager import KeyManager
from src.core.comment_manager import CommentConfig
from src.core.pipeline import AppConfig, Pipeline
from src.core.tts_base import SynthesisCancelled
from src.core.tts_manager import TTSConfig, resolve_voice
from src.ui.pages.page_create import PageCreate
from src.ui.wizard import WizardController

logger = logging.getLogger(__name__)

# Тексты заголовков на разных языках
APP_HEADER_TEXTS = {
    "ru": {
        "title": "Audiobook Generator",
        "subtitle": "Создание аудиокниг из FB2 с AI-комментариями",
    },
    "en": {
        "title": "Audiobook Generator",
        "subtitle": "Create audiobooks from FB2 with AI commentary",
    },
    "ja": {
        "title": "Audiobook Generator",
        "subtitle": "FB2からAIコメント付きオーディオブックを作成",
    },
    "zh": {
        "title": "Audiobook Generator",
        "subtitle": "从FB2创建带有AI评论的有声书",
    },
}


class AudiobookApp(ctk.CTk):
    """Главное окно приложения.

    Управляет пошаговым мастером (wizard) для настройки и запуска
    создания аудиокниги.

    ВАЖНО: ВСЯ коммуникация между pipeline-потоком и главным потоком tkinter
    происходит через очередь queue.Queue. Метод after() из не-main потока
    НЕ надёжен в PyInstaller --windowed сборках, поэтому progress_callback
    и обработчики ошибок кладут сообщения в очередь, а watchdog в главном
    потоке разбирает их и обновляет UI.
    """

    def __init__(self, settings: Optional[Settings] = None):
        super().__init__()

        self.settings = settings or load_settings()
        self.wizard: Optional[WizardController] = None
        self._pipeline: Optional[Pipeline] = None
        self._progress_page: Optional[PageCreate] = None
        self._pipeline_thread: Optional[threading.Thread] = None
        self._pipeline_started = False
        self._user_canceled = False
        # Единая очередь для ВСЕХ сообщений из pipeline-потока
        self._msg_queue: queue.Queue = queue.Queue()

        # Настройка окна
        self.title("Audiobook Generator")
        self.geometry(
            f"{self.settings.window_width}x{self.settings.window_height}"
        )
        self.minsize(800, 600)

        # Центрирование окна
        self.center_window()

        # Настройка внешнего вида
        ctk.set_appearance_mode("system")  # system, dark, light
        ctk.set_default_color_theme("blue")

        # Защита от закрытия во время работы
        self.protocol("WM_DELETE_WINDOW", self.on_closing)

        # Создание UI
        self._create_widgets()

        # Загрузка сохранённого API-ключа
        self._load_saved_key()

        logger.info("Приложение запущено")

    def _create_widgets(self):
        """Создание виджетов главного окна."""
        # Основной контейнер
        self.container = ctk.CTkFrame(self)
        self.container.pack(fill="both", expand=True, padx=10, pady=10)

        # Заголовок
        self.header_label = ctk.CTkLabel(
            self.container,
            text="Audiobook Generator",
            font=ctk.CTkFont(size=24, weight="bold"),
        )
        self.header_label.pack(pady=(10, 5))

        self.subtitle_label = ctk.CTkLabel(
            self.container,
            text="Создание аудиокниг из FB2 с AI-комментариями",
            font=ctk.CTkFont(size=14),
            text_color="gray",
        )
        self.subtitle_label.pack(pady=(0, 15))

        # Контейнер для страниц мастера
        self.wizard_frame = ctk.CTkFrame(self.container)
        self.wizard_frame.pack(fill="both", expand=True, padx=5, pady=5)

        # Создание контроллера мастера
        self.wizard = WizardController(
            parent=self.wizard_frame,
            settings=self.settings,
            on_complete=self.on_wizard_complete,
            on_lang_change=self.update_ui_language,
        )
        self.wizard.show_first_page()

    def update_ui_language(self):
        """Обновление языка интерфейса в заголовках окна."""
        t = APP_HEADER_TEXTS.get(self.settings.ui_lang, APP_HEADER_TEXTS["ru"])
        self.header_label.configure(text=t["title"])
        self.subtitle_label.configure(text=t["subtitle"])
        # Обновляем заголовок в мастере
        if self.wizard:
            self.wizard.update_step_language(self.settings.ui_lang)

    def _load_saved_key(self):
        """Загрузка сохранённого API-ключа."""
        if self.settings.ai_provider:
            key = KeyManager.load_key(self.settings.ai_provider)
            if key and self.wizard:
                self.wizard.set_api_key(key)

    def center_window(self):
        """Центрирование окна на экране."""
        self.update_idletasks()
        width = self.winfo_width()
        height = self.winfo_height()
        x = (self.winfo_screenwidth() // 2) - (width // 2)
        y = (self.winfo_screenheight() // 2) - (height // 2)
        self.geometry(f"{width}x{height}+{x}+{y}")

    def on_wizard_complete(self):
        """Старт создания: вызывается со шага «Создание» (PageCreate)."""
        if self._pipeline_thread and self._pipeline_thread.is_alive():
            logger.warning("Pipeline ещё работает или завершается — повторный старт пропущен")
            return

        logger.info("Запуск процесса создания аудиокниги")
        save_settings(self.settings)

        page = self.wizard.get_current_page() if self.wizard else None
        if not isinstance(page, PageCreate):
            logger.error("Шаг Создание не активен — pipeline не запущен")
            return

        self._progress_page = page
        self._pipeline_started = True
        self._user_canceled = False
        self._msg_queue = queue.Queue()

        page.set_pause_callback(self._on_pause)
        page.set_resume_callback(self._on_resume)
        page.set_cancel_callback(self._on_cancel)
        page._do_update_progress("Запуск процесса…", 0.0)

        self._pipeline_thread = threading.Thread(
            target=self._run_pipeline,
            daemon=True,
        )
        self._pipeline_thread.start()
        self.after(100, self._poll_msg_queue)

    def _poll_msg_queue(self):
        """Watchdog: разбор ВСЕХ сообщений из очереди pipeline-потока (в главном потоке).

        Вызывается по таймеру after() каждые 200мс, пока pipeline работает.
        pipeline-поток НИКОГДА не вызывает after() или методы tkinter напрямую —
        все сообщения кладутся в queue.Queue. Это единственный надёжный способ
        кросс-поточной коммуникации в PyInstaller --windowed сборке.
        """
        processed = False
        try:
            while True:
                msg = self._msg_queue.get_nowait()
                processed = True
                msg_type = msg[0]

                if msg_type == "progress":
                    # (type, status, progress, ...optional detail fields)
                    _, status, progress = msg[:3]
                    current_text = msg[3] if len(msg) > 3 else None
                    voice = msg[4] if len(msg) > 4 else None
                    engine = msg[5] if len(msg) > 5 else None
                    seg_idx = msg[6] if len(msg) > 6 else None
                    seg_total = msg[7] if len(msg) > 7 else None
                    stage = msg[8] if len(msg) > 8 else None
                    scope_line = msg[9] if len(msg) > 9 else None
                    if self._progress_page:
                        self._progress_page._do_update_progress(
                            status, progress,
                            current_text=current_text,
                            voice=voice,
                            engine=engine,
                            segment_index=seg_idx,
                            segment_total=seg_total,
                            stage=stage,
                            scope_line=scope_line,
                        )

                elif msg_type == "canceled":
                    _, cancel_msg = msg
                    logger.info("Pipeline отменён: %s", cancel_msg)
                    self._pipeline_started = False
                    if self._progress_page:
                        self._progress_page._do_update_progress(
                            f"❌ {cancel_msg}", 0.0,
                        )
                        self._progress_page.show_finished(ok=False)

                elif msg_type == "error":
                    # (type, err_msg)
                    _, err_msg = msg
                    logger.error("Ошибка pipeline: %s", err_msg)
                    self._pipeline_started = False
                    if self._progress_page:
                        self._progress_page._do_update_progress(
                            f"❌ Ошибка: {err_msg}", 0.0
                        )
                        self._progress_page.show_finished(ok=False)

                elif msg_type == "success":
                    # (type, message)
                    _, message = msg
                    logger.info("Pipeline завершён: %s", message)
                    self._pipeline_started = False
                    if self._progress_page:
                        self._progress_page._do_update_progress(
                            message, 1.0, stage="done",
                        )
                        self._progress_page.show_finished(ok=True)

                elif msg_type == "fatal":
                    # (type, err_msg)
                    _, err_msg = msg
                    logger.error("Критическая ошибка pipeline: %s", err_msg)
                    self._pipeline_started = False
                    if self._progress_page:
                        self._progress_page._do_update_progress(
                            f"❌ Критическая ошибка: {err_msg}", 0.0
                        )
                        self._progress_page.show_finished(ok=False)

        except queue.Empty:
            pass

        # Если поток ещё жив — продолжаем опрос
        if self._pipeline_thread and self._pipeline_thread.is_alive():
            self.after(200, self._poll_msg_queue)

    def _run_pipeline(self):
        """Запуск pipeline в отдельном потоке с собственным event loop.

        ВАЖНО: Этот метод НИКОГДА не вызывает after() или методы tkinter напрямую.
        Все сообщения для UI отправляются через self._msg_queue.
        """
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                loop.run_until_complete(self._run_pipeline_async())
            finally:
                loop.close()
        except Exception as e:
            err_msg = str(e)
            logger.error(
                "Критическая ошибка pipeline: %s\n%s", err_msg, traceback.format_exc()
            )
            # Отправляем через потокобезопасную очередь — НИКАКИХ after() из этого потока!
            self._msg_queue.put(("fatal", err_msg))

    async def _run_pipeline_async(self):
        """Асинхронный запуск pipeline.

        ВАЖНО: Все сообщения для UI отправляются через self._msg_queue.put().
        Методы after() и tkinter НЕ вызываются из этого контекста.
        """
        try:
            # Проверяем, что книга выбрана
            if not self.settings.book_path:
                raise ValueError("Не выбран FB2-файл")

            # Загружаем API-ключ
            api_key = KeyManager.load_key(self.settings.ai_provider) or ""

            # Создаём вложенные конфиги
            comment_config = CommentConfig(
                enabled=self.settings.comment_enabled,
                provider=self.settings.ai_provider,
                api_key=api_key,
                system_prompt=self.settings.system_prompt,
                frequency=self.settings.comment_frequency,
                max_concurrent=self.settings.max_concurrent,
            )
            tts_config = TTSConfig(
                backend=self.settings.tts_backend,
                main_voice=resolve_voice(
                    self.settings.tts_backend,
                    self.settings.book_lang,
                    self.settings.main_gender,
                ),
                comment_voice=resolve_voice(
                    self.settings.tts_backend,
                    self.settings.book_lang,
                    self.settings.comment_gender,
                ),
                main_speed=self.settings.main_speed,
                comment_speed=self.settings.comment_speed,
                pause_before_comment=self.settings.pause_before_comment,
                pause_after_comment=self.settings.pause_after_comment,
                pause_between_sentences=self.settings.pause_between_sentences,
            )

            config = AppConfig(
                book_path=Path(self.settings.book_path),
                output_dir=Path(self.settings.output_dir).expanduser(),
                lang=self.settings.book_lang,
                chapter_start=self.settings.chapter_start,
                chapter_end=self.settings.chapter_end,
                comment_config=comment_config,
                tts_config=tts_config,
            )

            self._pipeline = Pipeline(config)

            # Прогресс-колбэк — кладёт сообщения в queue.Queue, а НЕ вызывает tkinter!
            def progress_callback(status: str, progress: float, **details):
                self._msg_queue.put((
                    "progress",
                    status,
                    progress,
                    details.get("current_text"),
                    details.get("voice"),
                    details.get("engine"),
                    details.get("segment_index"),
                    details.get("segment_total"),
                    details.get("stage"),
                    details.get("scope_line"),
                ))

            # Детальный колбэк оставляем для совместимости бэкендов;
            # основной UI-прогресс по сегментам идёт через progress_callback из pipeline.
            def detail_callback(
                completed: int, total: int,
                text_preview: str, voice: str, backend_name: str,
            ):
                return

            await self._pipeline.run(
                progress_callback=progress_callback,
                detail_callback=detail_callback,
            )

            # Успешное завершение (в т.ч. частичный результат после отмены)
            self._msg_queue.put(("success", "✅ Аудиокнига создана!"))

        except SynthesisCancelled as e:
            logger.info("Создание отменено пользователем: %s", e)
            self._msg_queue.put(("canceled", str(e) or "Создание аудиокниги отменено"))

        except Exception as e:
            err_msg = str(e)
            if "отменен" in err_msg.lower():
                logger.info("Создание отменено: %s", err_msg)
                self._msg_queue.put(("canceled", err_msg))
            else:
                logger.error(
                    "Ошибка создания аудиокниги: %s\n%s", err_msg, traceback.format_exc()
                )
                self._msg_queue.put(("error", err_msg))

    def _on_pause(self):
        """Обработчик паузы."""
        if self._pipeline:
            self._pipeline.pause()
            logger.info("Pipeline приостановлен")

    def _on_resume(self):
        """Обработчик возобновления."""
        if self._pipeline:
            self._pipeline.resume()
            logger.info("Pipeline возобновлён")

    def _on_cancel(self):
        """Отмена: флаг + дождаться конца текущей главы и частичной склейки."""
        self._user_canceled = True
        if self._pipeline:
            self._pipeline.cancel()
            logger.info("Pipeline: запрошена отмена (после текущей главы)")
        if self._progress_page:
            self._progress_page.set_canceling()

    def on_closing(self):
        """Обработчик закрытия окна."""
        # Если pipeline запущен — отменяем
        if self._pipeline:
            self._pipeline.cancel()
        # Сохраняем настройки
        save_settings(self.settings)
        self.destroy()

    def run(self):
        """Запуск главного цикла приложения."""
        self.mainloop()
