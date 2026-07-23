"""
Шаг 7: Финальная страница с кнопкой запуска создания аудиокниги.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable, Optional

import customtkinter as ctk

from src.config.settings import Settings
from src.core.fb2_parser import FB2Parser
from src.core.tts_manager import resolve_voice
from src.utils.scope_display import format_scope

logger = logging.getLogger(__name__)

# Тексты на разных языках
LAUNCH_TEXTS = {
    "ru": {
        "title": "Всё готово к запуску!",
        "desc": "Проверьте настройки и нажмите кнопку для создания аудиокниги",
        "summary_title": "Сводка настроек:",
        "lang_label": "Язык книг",
        "provider_label": "AI-провайдер",
        "freq_label": "Комментарии",
        "freq_format": "каждые {} предложений",
        "freq_off": "Отключены",
        "scope_label": "Объём озвучки",
        "total_chapters_label": "Всего глав в файле",
        "total_chapters_unknown": "—",
        "voice_main_label": "Голос текста",
        "voice_comment_label": "Голос комментатора",
        "output_label": "Путь сохранения",
        "tts_label": "TTS движок",
        "launch_default": "Создать аудиокнигу",
        "warning": "Процесс может занять продолжительное время в зависимости\n"
                   "от размера книги и скорости API",
    },
    "en": {
        "title": "Ready to launch!",
        "desc": "Check your settings and click the button to create the audiobook",
        "summary_title": "Settings summary:",
        "lang_label": "Book language",
        "provider_label": "AI Provider",
        "freq_label": "Comments",
        "freq_format": "every {} sentences",
        "freq_off": "Disabled",
        "scope_label": "Narration scope",
        "total_chapters_label": "Total chapters in file",
        "total_chapters_unknown": "—",
        "voice_main_label": "Text voice",
        "voice_comment_label": "Comment voice",
        "output_label": "Output path",
        "tts_label": "TTS engine",
        "launch_default": "Create audiobook",
        "warning": "The process may take a long time depending\n"
                   "on the book size and API speed",
    },
    "ja": {
        "title": "準備完了！",
        "desc": "設定を確認し、ボタンをクリックしてオーディオブックを作成してください",
        "summary_title": "設定概要:",
        "lang_label": "書籍の言語",
        "provider_label": "AIプロバイダー",
        "freq_label": "コメント",
        "freq_format": "{}文ごと",
        "freq_off": "無効",
        "scope_label": "ナレーション範囲",
        "total_chapters_label": "ファイル内の総章数",
        "total_chapters_unknown": "—",
        "voice_main_label": "テキストの声",
        "voice_comment_label": "コメントの声",
        "output_label": "出力先",
        "tts_label": "TTSエンジン",
        "launch_default": "オーディオブックを作成",
        "warning": "処理には書籍のサイズとAPIの速度に応じて\n時間がかかる場合があります",
    },
    "zh": {
        "title": "准备就绪！",
        "desc": "检查设置并点击按钮创建有声书",
        "summary_title": "设置摘要：",
        "lang_label": "书籍语言",
        "provider_label": "AI提供商",
        "freq_label": "评论",
        "freq_format": "每{}句",
        "freq_off": "已禁用",
        "scope_label": "朗读范围",
        "total_chapters_label": "文件中总章节数",
        "total_chapters_unknown": "—",
        "voice_main_label": "正文语音",
        "voice_comment_label": "评论语音",
        "output_label": "输出路径",
        "tts_label": "TTS引擎",
        "launch_default": "创建有声书",
        "warning": "处理时间可能因书籍大小和API速度\n而有所不同",
    },
}

# Маппинг кодов TTS бэкендов на отображаемые названия
TTS_BACKEND_NAMES = {
    "edge": "Edge TTS",
    "piper": "Piper (локальный)",
    "supertonic": "Supertonic 3 (локальный)",
    "silero": "Silero TTS (локальный)",
}


class PageLaunch(ctk.CTkFrame):
    """Финальная страница мастера с кнопкой запуска."""

    def __init__(
        self,
        parent: ctk.CTkFrame,
        settings: Settings,
        on_complete: Optional[Callable] = None,
    ):
        super().__init__(parent)
        self.settings = settings
        self.on_complete = on_complete
        self._total_chapters: Optional[int] = self._count_chapters()

        self._create_widgets()

    def _create_widgets(self):
        """Создание виджетов страницы."""
        lang = self.settings.ui_lang
        t = LAUNCH_TEXTS.get(lang, LAUNCH_TEXTS["ru"])

        # Заголовок
        title = ctk.CTkLabel(
            self,
            text=t["title"],
            font=ctk.CTkFont(size=20, weight="bold"),
        )
        title.pack(pady=(30, 10))

        desc = ctk.CTkLabel(
            self,
            text=t["desc"],
            font=ctk.CTkFont(size=13),
            text_color="gray",
        )
        desc.pack(pady=(0, 20))

        # Сводка настроек
        summary_frame = ctk.CTkFrame(self)
        summary_frame.pack(fill="x", padx=40, pady=10)

        ctk.CTkLabel(
            summary_frame,
            text=t["summary_title"],
            font=ctk.CTkFont(size=14, weight="bold"),
        ).pack(anchor="w", padx=10, pady=(10, 5))

        # Создаём строки сводки
        self.summary_items = []

        tts_backend_display = TTS_BACKEND_NAMES.get(
            self.settings.tts_backend, self.settings.tts_backend
        )

        total_display = (
            str(self._total_chapters)
            if self._total_chapters is not None
            else t["total_chapters_unknown"]
        )

        items = [
            (t["lang_label"], self._lang_display(self.settings.book_lang)),
            (t["provider_label"], self.settings.ai_provider.capitalize()),
            (t["freq_label"], self._comment_summary()),
            (t["scope_label"], self._scope_summary()),
            (t["total_chapters_label"], total_display),
            (t["voice_main_label"], resolve_voice(
                self.settings.tts_backend, self.settings.book_lang, self.settings.main_gender,
            )),
            (t["voice_comment_label"], resolve_voice(
                self.settings.tts_backend, self.settings.book_lang, self.settings.comment_gender,
            )),
            (t["tts_label"], tts_backend_display),
            (t["output_label"], self.settings.output_dir),
        ]

        for label, value in items:
            item_frame = ctk.CTkFrame(summary_frame)
            item_frame.pack(fill="x", padx=10, pady=2)

            ctk.CTkLabel(
                item_frame,
                text=f"{label}:",
                font=ctk.CTkFont(size=13),
                width=180,
                anchor="w",
            ).pack(side="left", padx=5, pady=2)

            ctk.CTkLabel(
                item_frame,
                text=value,
                font=ctk.CTkFont(size=13),
                text_color="gray",
                anchor="w",
            ).pack(side="left", padx=5, pady=2)

        # Предупреждение
        warning = ctk.CTkLabel(
            self,
            text=t["warning"],
            font=ctk.CTkFont(size=12),
            text_color="orange",
            justify="center",
        )
        warning.pack(pady=(20, 10))

    def _lang_display(self, code: str) -> str:
        """Конвертация кода языка в отображаемое название."""
        mapping = {
            "ru": "Русский",
            "en": "English",
            "ja": "日本語",
            "zh": "中文",
        }
        return mapping.get(code, code)

    def _comment_summary(self) -> str:
        """Отображение статуса комментариев."""
        if not self.settings.comment_enabled:
            lang = self.settings.ui_lang
            t = LAUNCH_TEXTS.get(lang, LAUNCH_TEXTS["ru"])
            return t["freq_off"]
        lang = self.settings.ui_lang
        t = LAUNCH_TEXTS.get(lang, LAUNCH_TEXTS["ru"])
        return t["freq_format"].format(self.settings.comment_frequency)

    def _count_chapters(self) -> Optional[int]:
        """Число глав в выбранном FB2 (для сводки)."""
        book_path = getattr(self.settings, "book_path", "") or ""
        if not book_path:
            return None
        path = Path(book_path)
        if not path.exists():
            return None
        try:
            book = FB2Parser().parse(path)
            return len(book.chapters)
        except Exception as exc:
            logger.warning("Не удалось посчитать главы для сводки: %s", exc)
            return None

    def _scope_summary(self) -> str:
        """Человекочитаемый объём озвучки по chapter_start/chapter_end."""
        return format_scope(
            int(getattr(self.settings, "chapter_start", 0) or 0),
            int(getattr(self.settings, "chapter_end", 0) or 0),
            self._total_chapters,
            self.settings.ui_lang,
        )

    def get_data(self) -> dict:
        """Сбор данных со страницы."""
        return {}

    def validate(self) -> bool:
        """Валидация данных страницы."""
        return True
