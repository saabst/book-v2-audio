"""
Шаг 6: Настройка объёма озвучки (одна глава, диапазон, вся книга).
"""

from __future__ import annotations

from typing import Callable, Optional

import customtkinter as ctk

from src.config.settings import Settings

# Тексты на разных языках
SCOPE_TEXTS = {
    "ru": {
        "title": "Шаг 6: Объём озвучки",
        "desc": "Выберите, какие главы книги нужно озвучить",
        "all": "Вся книга",
        "range": "Диапазон глав",
        "single": "Одна глава",
        "from": "С главы:",
        "to": "По главу:",
        "chapter_num": "Номер главы:",
    },
    "en": {
        "title": "Step 6: Narration Scope",
        "desc": "Select which chapters of the book to narrate",
        "all": "Whole book",
        "range": "Chapter range",
        "single": "Single chapter",
        "from": "From chapter:",
        "to": "To chapter:",
        "chapter_num": "Chapter number:",
    },
    "ja": {
        "title": "ステップ6: ナレーション範囲",
        "desc": "ナレーションする章を選択してください",
        "all": "全章",
        "range": "章の範囲",
        "single": "1つの章",
        "from": "開始章:",
        "to": "終了章:",
        "chapter_num": "章番号:",
    },
    "zh": {
        "title": "步骤6：朗读范围",
        "desc": "选择需要朗读的书籍章节",
        "all": "全书",
        "range": "章节范围",
        "single": "单章",
        "from": "从章节：",
        "to": "到章节：",
        "chapter_num": "章节号：",
    },
}


class PageScope(ctk.CTkFrame):
    """Страница настройки объёма озвучки."""

    def __init__(
        self,
        parent: ctk.CTkFrame,
        settings: Settings,
        on_complete: Optional[Callable] = None,
    ):
        super().__init__(parent)
        self.settings = settings
        self.on_complete = on_complete

        self._create_widgets()

    def _create_widgets(self):
        """Создание виджетов страницы."""
        lang = self.settings.ui_lang
        t = SCOPE_TEXTS.get(lang, SCOPE_TEXTS["ru"])

        # Заголовок
        title = ctk.CTkLabel(
            self,
            text=t["title"],
            font=ctk.CTkFont(size=18, weight="bold"),
        )
        title.pack(pady=(20, 10))

        desc = ctk.CTkLabel(
            self,
            text=t["desc"],
            font=ctk.CTkFont(size=13),
            text_color="gray",
        )
        desc.pack(pady=(0, 20))

        # Варианты объёма
        scope_frame = ctk.CTkFrame(self)
        scope_frame.pack(fill="x", padx=40, pady=10)

        self.scope_var = ctk.StringVar(value="all")

        # Вся книга
        all_radio = ctk.CTkRadioButton(
            scope_frame,
            text=t["all"],
            variable=self.scope_var,
            value="all",
            command=self._on_scope_change,
        )
        all_radio.pack(anchor="w", padx=10, pady=8)

        # Диапазон глав
        range_radio = ctk.CTkRadioButton(
            scope_frame,
            text=t["range"],
            variable=self.scope_var,
            value="range",
            command=self._on_scope_change,
        )
        range_radio.pack(anchor="w", padx=10, pady=8)

        # Одна глава
        single_radio = ctk.CTkRadioButton(
            scope_frame,
            text=t["single"],
            variable=self.scope_var,
            value="single",
            command=self._on_scope_change,
        )
        single_radio.pack(anchor="w", padx=10, pady=8)

        # Настройки диапазона
        self.range_frame = ctk.CTkFrame(self)
        self.range_frame.pack(fill="x", padx=40, pady=10)
        self.range_frame.pack_forget()  # скрыто по умолчанию

        range_inner = ctk.CTkFrame(self.range_frame)
        range_inner.pack(padx=10, pady=10)

        ctk.CTkLabel(
            range_inner,
            text=t["from"],
            font=ctk.CTkFont(size=13),
        ).grid(row=0, column=0, padx=5, pady=5)

        self.start_chapter_var = ctk.StringVar(value="1")
        self.start_chapter_entry = ctk.CTkEntry(
            range_inner,
            textvariable=self.start_chapter_var,
            width=60,
        )
        self.start_chapter_entry.grid(row=0, column=1, padx=5, pady=5)

        ctk.CTkLabel(
            range_inner,
            text=t["to"],
            font=ctk.CTkFont(size=13),
        ).grid(row=0, column=2, padx=5, pady=5)

        self.end_chapter_var = ctk.StringVar(value="1")
        self.end_chapter_entry = ctk.CTkEntry(
            range_inner,
            textvariable=self.end_chapter_var,
            width=60,
        )
        self.end_chapter_entry.grid(row=0, column=3, padx=5, pady=5)

        # Настройки одной главы
        self.single_frame = ctk.CTkFrame(self)
        self.single_frame.pack(fill="x", padx=40, pady=10)
        self.single_frame.pack_forget()  # скрыто по умолчанию

        ctk.CTkLabel(
            self.single_frame,
            text=t["chapter_num"],
            font=ctk.CTkFont(size=13),
        ).pack(side="left", padx=10, pady=10)

        self.single_chapter_var = ctk.StringVar(value="1")
        self.single_chapter_entry = ctk.CTkEntry(
            self.single_frame,
            textvariable=self.single_chapter_var,
            width=60,
        )
        self.single_chapter_entry.pack(side="left", padx=5, pady=10)

    def _on_scope_change(self):
        """Обработчик изменения выбора объёма."""
        scope = self.scope_var.get()
        if scope == "range":
            self.range_frame.pack(fill="x", padx=40, pady=10)
            self.single_frame.pack_forget()
        elif scope == "single":
            self.single_frame.pack(fill="x", padx=40, pady=10)
            self.range_frame.pack_forget()
        else:
            self.range_frame.pack_forget()
            self.single_frame.pack_forget()

    def get_data(self) -> dict:
        """Сбор данных со страницы."""
        scope = self.scope_var.get()
        data = {}

        if scope == "all":
            data["chapter_start"] = 0
            data["chapter_end"] = 0
        elif scope == "range":
            try:
                data["chapter_start"] = max(0, int(self.start_chapter_var.get()) - 1)
                data["chapter_end"] = int(self.end_chapter_var.get())
            except ValueError:
                data["chapter_start"] = 0
                data["chapter_end"] = 0
        elif scope == "single":
            try:
                chapter = max(0, int(self.single_chapter_var.get()) - 1)
                data["chapter_start"] = chapter
                data["chapter_end"] = chapter + 1
            except ValueError:
                data["chapter_start"] = 0
                data["chapter_end"] = 1

        return data

    def validate(self) -> bool:
        """Валидация данных страницы."""
        scope = self.scope_var.get()
        if scope == "range":
            try:
                start = int(self.start_chapter_var.get())
                end = int(self.end_chapter_var.get())
                if start < 1 or end < 1 or start > end:
                    return False
            except ValueError:
                return False
        elif scope == "single":
            try:
                chapter = int(self.single_chapter_var.get())
                if chapter < 1:
                    return False
            except ValueError:
                return False
        return True
