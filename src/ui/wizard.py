"""
Контроллер пошагового мастера настройки.
Управляет переключением между страницами и сбором данных.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable, List, Optional, Type

import customtkinter as ctk

from src.config.settings import Settings
from src.ui.pages import (
    PageLanguage,
    PageAPI,
    PageLogo,
    PageFile,
    PageScope,
    PageComments,
    PageLaunch,
)

logger = logging.getLogger(__name__)

# Названия шагов на разных языках
STEP_NAMES = {
    "ru": ["Язык", "API", "О нас", "Файл", "Комментарии", "Объём", "Запуск"],
    "en": ["Language", "API", "About", "File", "Comments", "Scope", "Launch"],
    "ja": ["言語", "API", "概要", "ファイル", "コメント", "範囲", "開始"],
    "zh": ["语言", "API", "关于", "文件", "评论", "范围", "启动"],
}

# Тексты навигационных кнопок
NAV_TEXTS = {
    "ru": {"back": "← Назад", "next": "Далее →"},
    "en": {"back": "← Back", "next": "Next →"},
    "ja": {"back": "← 戻る", "next": "次へ →"},
    "zh": {"back": "← 返回", "next": "下一步 →"},
}


class WizardController:
    """Контроллер пошагового мастера.

    Управляет последовательностью страниц, навигацией и сбором данных.
    """

    def __init__(
        self,
        parent: ctk.CTkFrame,
        settings: Settings,
        on_complete: Optional[Callable] = None,
        on_lang_change: Optional[Callable[[str], None]] = None,
    ):
        self.parent = parent
        self.settings = settings
        self.on_complete = on_complete
        self.on_lang_change = on_lang_change
        self.current_page_index = 0
        self.pages: List[ctk.CTkFrame] = []

        # Индикатор шагов
        self.steps_frame = ctk.CTkFrame(parent, height=40)
        self.steps_frame.pack(fill="x", padx=5, pady=(5, 0))
        self.steps_frame.pack_propagate(False)

        self.step_labels: List[ctk.CTkLabel] = []
        self._create_step_indicator()

        # Контейнер для содержимого страницы
        self.content_frame = ctk.CTkFrame(parent)
        self.content_frame.pack(fill="both", expand=True, padx=5, pady=5)

        # Навигационные кнопки
        self.nav_frame = ctk.CTkFrame(parent, height=50)
        self.nav_frame.pack(fill="x", padx=5, pady=(0, 5))
        self.nav_frame.pack_propagate(False)

        nav_t = NAV_TEXTS.get(self.settings.ui_lang, NAV_TEXTS["ru"])
        self.back_btn = ctk.CTkButton(
            self.nav_frame,
            text=nav_t["back"],
            command=self.go_back,
            state="disabled",
            width=100,
        )
        self.back_btn.pack(side="left", padx=10, pady=5)

        self.next_btn = ctk.CTkButton(
            self.nav_frame,
            text=nav_t["next"],
            command=self.go_next,
            width=100,
        )
        self.next_btn.pack(side="right", padx=10, pady=5)

    def _create_step_indicator(self):
        """Создание индикатора шагов вверху мастера."""
        lang = self.settings.ui_lang
        step_names = STEP_NAMES.get(lang, STEP_NAMES["ru"])
        for i, name in enumerate(step_names):
            label = ctk.CTkLabel(
                self.steps_frame,
                text=f"{i + 1}. {name}",
                font=ctk.CTkFont(size=11),
            )
            label.pack(side="left", padx=8, pady=5, expand=True)
            self.step_labels.append(label)

    def update_step_language(self, lang_code: str):
        """Обновление языка шагов и навигационных кнопок."""
        step_names = STEP_NAMES.get(lang_code, STEP_NAMES["ru"])
        for i, label in enumerate(self.step_labels):
            if i < len(step_names):
                label.configure(text=f"{i + 1}. {step_names[i]}")

        # Обновляем навигационные кнопки
        nav_t = NAV_TEXTS.get(lang_code, NAV_TEXTS["ru"])
        self.back_btn.configure(text=nav_t["back"])
        # Обновляем кнопку "Далее" только если она видна (не на последней странице)
        try:
            if self.next_btn.winfo_viewable():
                self.next_btn.configure(text=nav_t["next"])
        except Exception:
            pass

    def show_first_page(self):
        """Показать первую страницу."""
        self._show_page(0)

    def _show_page(self, index: int):
        """Показать страницу с указанным индексом."""
        # Очищаем контейнер
        for widget in self.content_frame.winfo_children():
            widget.destroy()

        # Создаём страницу
        page_classes = [
            PageLanguage,
            PageAPI,
            PageLogo,
            PageFile,
            PageComments,
            PageScope,
            PageLaunch,
        ]

        if index < 0 or index >= len(page_classes):
            return

        page_class = page_classes[index]
        is_last = (index == len(page_classes) - 1)

        # Для последней страницы передаём on_complete (запуск pipeline),
        # для остальных — _on_page_complete (сохранение данных)
        page_callback = self.on_complete if is_last else self._on_page_complete

        # Для страницы языка передаём дополнительный колбэк смены языка
        kwargs = {}
        if index == 0 and self.on_lang_change:
            kwargs["on_lang_change"] = self.on_lang_change

        page = page_class(
            self.content_frame,
            self.settings,
            page_callback,
            **kwargs,
        )
        page.pack(fill="both", expand=True)

        self.current_page_index = index

        # Обновляем индикатор шагов
        self._update_step_indicator(index)

        # Обновляем кнопки навигации
        self.back_btn.configure(
            state="normal" if index > 0 else "disabled"
        )

        if is_last:
            # На последней странице кнопка навигации не нужна —
            # на самой странице есть зелёная кнопка запуска
            self.next_btn.pack_forget()
        else:
            self.next_btn.pack(side="right", padx=10, pady=5)
            nav_t = NAV_TEXTS.get(self.settings.ui_lang, NAV_TEXTS["ru"])
            self.next_btn.configure(text=nav_t["next"])

    def _update_step_indicator(self, active_index: int):
        """Обновление подсветки активного шага."""
        for i, label in enumerate(self.step_labels):
            if i == active_index:
                label.configure(text_color="#3B8ED0")  # активный
            elif i < active_index:
                label.configure(text_color="green")  # пройденный
            else:
                label.configure(text_color="gray")  # будущий

    def _on_page_complete(self, data: dict):
        """Обработчик завершения страницы."""
        # Обновляем настройки из данных страницы
        for key, value in data.items():
            if hasattr(self.settings, key):
                setattr(self.settings, key, value)

    def go_back(self):
        """Переход на предыдущую страницу."""
        if self.current_page_index > 0:
            self._show_page(self.current_page_index - 1)

    def go_next(self):
        """Переход на следующую страницу."""
        # Вызываем валидацию текущей страницы
        current_page = self._get_current_page()
        if current_page and hasattr(current_page, "validate"):
            if not current_page.validate():
                return

        # Собираем данные с текущей страницы
        if current_page and hasattr(current_page, "get_data"):
            data = current_page.get_data()
            self._on_page_complete(data)

        max_index = 6  # 7 страниц (0-6)
        if self.current_page_index < max_index:
            self._show_page(self.current_page_index + 1)
        else:
            # Последняя страница — запуск
            # Данные уже собраны через _on_page_complete выше
            if self.on_complete:
                self.on_complete()

    def _get_current_page(self):
        """Получение ссылки на текущую страницу."""
        children = self.content_frame.winfo_children()
        return children[0] if children else None

    def set_api_key(self, key: str):
        """Установка API-ключа (вызывается при загрузке сохранённого)."""
        # Пробуем найти страницу API и установить ключ
        for page in self.pages:
            if hasattr(page, "set_api_key"):
                page.set_api_key(key)
                break
