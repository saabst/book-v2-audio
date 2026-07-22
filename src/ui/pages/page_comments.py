"""
Шаг 5: Настройка частоты комментирования, выбор системного промпта и TTS-движка.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable, Dict, Optional

import tomllib as tomli
import customtkinter as ctk

from src.config.settings import Settings
from src.core.tts_manager import resolve_voice

logger = logging.getLogger(__name__)

# Тексты на разных языках
COMMENTS_TEXTS = {
    "ru": {
        "title": "Шаг 5: Настройка комментариев",
        "desc": "Настройте частоту комментариев и выберите роль комментатора",
        "enabled_label": "Генерировать AI-комментарии",
        "freq_label": "Частота комментирования:",
        "freq_format": "каждые {} предложений",
        "role_label": "Роль комментатора:",
        "custom_label": "Или введите свой системный промпт:",
        "tts_label": "TTS движок:",
        "tts_desc": "Edge TTS — облачный, высокое качество, возможны сбои.\\nPiper — локальный, на CPU, без интернета, чуть ниже качество.\\nSupertonic 3 — локальный, отличное качество, 31 язык, ~305 МБ.\\nSilero TTS v5 — локальный, лучшее качество русского среди open-source.",
        "gender_main_label": "Пол основного голоса:",
        "gender_comment_label": "Пол голоса комментатора:",
        "gender_male": "Мужской",
        "gender_female": "Женский",
    },
    "en": {
        "title": "Step 5: Comment Settings",
        "desc": "Set comment frequency and choose a commentator role",
        "enabled_label": "Generate AI comments",
        "freq_label": "Comment frequency:",
        "freq_format": "every {} sentences",
        "role_label": "Commentator role:",
        "custom_label": "Or enter your own system prompt:",
        "tts_label": "TTS engine:",
        "tts_desc": "Edge TTS — cloud-based, high quality, but may have outages.\\nPiper — local, CPU-only, no internet needed, slightly lower quality.\\nSupertonic 3 — local, high quality, 31 languages, ~305 MB.\\nSilero TTS v5 — local, best russian quality among open-source.",
        "gender_main_label": "Main voice gender:",
        "gender_comment_label": "Comment voice gender:",
        "gender_male": "Male",
        "gender_female": "Female",
    },
    "ja": {
        "title": "ステップ5: コメント設定",
        "desc": "コメント頻度とコメンテーターの役割を設定してください",
        "enabled_label": "AIコメントを生成",
        "freq_label": "コメント頻度:",
        "freq_format": "{}文ごと",
        "role_label": "コメンテーターの役割:",
        "custom_label": "または独自のシステムプロンプトを入力:",
        "tts_label": "TTSエンジン:",
        "tts_desc": "Edge TTS — クラウド、高品質だが障害の可能性あり。\\nPiper — ローカル、CPU動作、オフラインでも使用可能。\\nSupertonic 3 — ローカル、高品質、31言語、~305 MB。\\nSilero TTS v5 — ローカル、ロシア語に最適なオープンソースTTS。",
        "gender_main_label": "本文声の性別:",
        "gender_comment_label": "コメント声の性別:",
        "gender_male": "男性",
        "gender_female": "女性",
    },
    "zh": {
        "title": "步骤5：评论设置",
        "desc": "设置评论频率并选择评论者角色",
        "enabled_label": "生成AI评论",
        "freq_label": "评论频率：",
        "freq_format": "每{}句",
        "role_label": "评论者角色：",
        "custom_label": "或输入您自己的系统提示：",
        "tts_label": "TTS引擎：",
        "tts_desc": "Edge TTS — 云端，高质量，但可能中断。\\nPiper — 本地，CPU运行，无需网络，质量稍低。\\nSupertonic 3 — 本地，高质量，31种语言，~305 MB。\\nSilero TTS v5 — 本地，开源中俄语质量最佳。",
        "gender_main_label": "正文语音性别：",
        "gender_comment_label": "评论语音性别：",
        "gender_male": "男",
        "gender_female": "女",
    },
}


class PageComments(ctk.CTkFrame):
    """Страница настройки комментариев."""

    def __init__(
        self,
        parent: ctk.CTkFrame,
        settings: Settings,
        on_complete: Optional[Callable] = None,
    ):
        super().__init__(parent)
        self.settings = settings
        self.on_complete = on_complete
        self.prompts: Dict[str, dict] = {}

        self._create_widgets()
        self._load_prompts()

    def _create_widgets(self):
        """Создание виджетов страницы."""
        lang = self.settings.ui_lang
        t = COMMENTS_TEXTS.get(lang, COMMENTS_TEXTS["ru"])

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
        desc.pack(pady=(0, 10))

        # Чекбокс включения комментариев
        self.comments_enabled_var = ctk.BooleanVar(value=self.settings.comment_enabled)
        self.enabled_check = ctk.CTkCheckBox(
            self,
            text=t["enabled_label"],
            variable=self.comments_enabled_var,
            command=self._on_enabled_toggle,
            font=ctk.CTkFont(size=14),
        )
        self.enabled_check.pack(anchor="w", padx=40, pady=(0, 10))

        # Частота комментирования
        self.freq_frame = ctk.CTkFrame(self)
        self.freq_frame.pack(fill="x", padx=40, pady=10)

        ctk.CTkLabel(
            self.freq_frame,
            text=t["freq_label"],
            font=ctk.CTkFont(size=14),
        ).pack(anchor="w", padx=10, pady=(10, 5))

        freq_inner = ctk.CTkFrame(self.freq_frame)
        freq_inner.pack(fill="x", padx=10, pady=(0, 10))

        self.frequency_var = ctk.StringVar(value=str(self.settings.comment_frequency))
        freq_slider = ctk.CTkSlider(
            freq_inner,
            from_=1,
            to=20,
            number_of_steps=19,
            command=self._on_freq_change,
        )
        freq_slider.set(self.settings.comment_frequency)
        freq_slider.pack(side="left", padx=(0, 10), fill="x", expand=True)

        self.freq_label = ctk.CTkLabel(
            freq_inner,
            text=t["freq_format"].format(self.settings.comment_frequency),
            font=ctk.CTkFont(size=13),
            width=200,
        )
        self.freq_label.pack(side="left")

        # Выбор роли комментатора
        self.role_frame = ctk.CTkFrame(self)
        self.role_frame.pack(fill="x", padx=40, pady=10)

        ctk.CTkLabel(
            self.role_frame,
            text=t["role_label"],
            font=ctk.CTkFont(size=14),
        ).pack(anchor="w", padx=10, pady=(10, 5))

        self.role_var = ctk.StringVar(value="")
        self.role_menu = ctk.CTkOptionMenu(
            self.role_frame,
            values=[],
            variable=self.role_var,
            command=self._on_role_change,
            width=300,
        )
        self.role_menu.pack(anchor="w", padx=10, pady=(0, 5))

        # Описание роли
        self.role_desc_label = ctk.CTkLabel(
            self.role_frame,
            text="",
            font=ctk.CTkFont(size=12),
            text_color="gray",
            wraplength=500,
            justify="left",
        )
        self.role_desc_label.pack(anchor="w", padx=10, pady=(0, 10))

        # Свой промпт
        self.custom_frame = ctk.CTkFrame(self)
        self.custom_frame.pack(fill="x", padx=40, pady=10)

        ctk.CTkLabel(
            self.custom_frame,
            text=t["custom_label"],
            font=ctk.CTkFont(size=14),
        ).pack(anchor="w", padx=10, pady=(10, 5))

        self.custom_prompt_text = ctk.CTkTextbox(
            self.custom_frame,
            height=100,
            width=500,
        )
        self.custom_prompt_text.pack(anchor="w", padx=10, pady=(0, 10))

        if self.settings.system_prompt:
            self.custom_prompt_text.insert("1.0", self.settings.system_prompt)

        # --- TTS движок ---
        tts_frame = ctk.CTkFrame(self)
        tts_frame.pack(fill="x", padx=40, pady=10)

        ctk.CTkLabel(
            tts_frame,
            text=t["tts_label"],
            font=ctk.CTkFont(size=14),
        ).pack(anchor="w", padx=10, pady=(10, 5))

        self.tts_backend_var = ctk.StringVar(value=self.settings.tts_backend)
        self.tts_backend_menu = ctk.CTkOptionMenu(
            tts_frame,
            values=["edge", "piper", "supertonic", "silero"],
            variable=self.tts_backend_var,
            command=self._on_tts_backend_change,
            width=300,
        )
        self.tts_backend_menu.pack(anchor="w", padx=10, pady=(0, 5))

        # Описание TTS движка
        self.tts_desc_label = ctk.CTkLabel(
            tts_frame,
            text=t["tts_desc"],
            font=ctk.CTkFont(size=12),
            text_color="gray",
            wraplength=500,
            justify="left",
        )
        self.tts_desc_label.pack(anchor="w", padx=10, pady=(0, 10))

        # --- Пол голоса ---
        gender_frame = ctk.CTkFrame(self)
        gender_frame.pack(fill="x", padx=40, pady=10)

        ctk.CTkLabel(
            gender_frame,
            text=t["gender_main_label"],
            font=ctk.CTkFont(size=14),
        ).pack(anchor="w", padx=10, pady=(10, 5))

        gender_inner = ctk.CTkFrame(gender_frame)
        gender_inner.pack(fill="x", padx=10, pady=(0, 5))

        self.main_gender_var = ctk.StringVar(value=self.settings.main_gender)
        self.main_gender_male = ctk.CTkRadioButton(
            gender_inner,
            text=t["gender_male"],
            variable=self.main_gender_var,
            value="male",
        )
        self.main_gender_male.pack(side="left", padx=(0, 20))
        self.main_gender_female = ctk.CTkRadioButton(
            gender_inner,
            text=t["gender_female"],
            variable=self.main_gender_var,
            value="female",
        )
        self.main_gender_female.pack(side="left")

        ctk.CTkLabel(
            gender_frame,
            text=t["gender_comment_label"],
            font=ctk.CTkFont(size=14),
        ).pack(anchor="w", padx=10, pady=(10, 5))

        gender_comment_inner = ctk.CTkFrame(gender_frame)
        gender_comment_inner.pack(fill="x", padx=10, pady=(0, 10))

        self.comment_gender_var = ctk.StringVar(value=self.settings.comment_gender)
        self.comment_gender_male = ctk.CTkRadioButton(
            gender_comment_inner,
            text=t["gender_male"],
            variable=self.comment_gender_var,
            value="male",
        )
        self.comment_gender_male.pack(side="left", padx=(0, 20))
        self.comment_gender_female = ctk.CTkRadioButton(
            gender_comment_inner,
            text=t["gender_female"],
            variable=self.comment_gender_var,
            value="female",
        )
        self.comment_gender_female.pack(side="left")

        # Применяем начальное состояние чекбокса к фреймам
        self._on_enabled_toggle()

    def _load_prompts(self):
        """Загрузка заготовок промптов из TOML-файла."""
        prompts_paths = [
            Path("resources/prompts.toml"),
            Path("../resources/prompts.toml"),
        ]

        for path in prompts_paths:
            if path.exists():
                try:
                    with open(path, "rb") as f:
                        data = tomli.load(f)
                    self.prompts = data.get("prompts", {})
                    break
                except Exception as e:
                    logger.warning("Ошибка загрузки промптов: %s", e)

        # Заполняем меню ролей
        role_names = []
        for key, prompt_data in self.prompts.items():
            role_names.append(prompt_data.get("name", key))

        if role_names:
            self.role_menu.configure(values=role_names)
            self.role_var.set(role_names[0])
            # Показываем описание первой роли
            first_key = list(self.prompts.keys())[0]
            self.role_desc_label.configure(
                text=self.prompts[first_key].get("description", "")
            )

    def _on_freq_change(self, value: float):
        """Обработчик изменения частоты."""
        freq = int(value)
        lang = self.settings.ui_lang
        t = COMMENTS_TEXTS.get(lang, COMMENTS_TEXTS["ru"])
        self.freq_label.configure(text=t["freq_format"].format(freq))
        self.frequency_var.set(str(freq))

    def _on_role_change(self, role_name: str):
        """Обработчик выбора роли."""
        for key, prompt_data in self.prompts.items():
            if prompt_data.get("name") == role_name:
                desc = prompt_data.get("description", "")
                self.role_desc_label.configure(text=desc)
                # Заполняем текстовое поле промптом
                self.custom_prompt_text.delete("1.0", "end")
                self.custom_prompt_text.insert("1.0", prompt_data.get("text", ""))
                break

    def _on_enabled_toggle(self):
        """Обработчик переключения чекбокса генерации комментариев."""
        enabled = self.comments_enabled_var.get()
        state = "normal" if enabled else "disabled"
        for widget in [self.freq_frame, self.role_frame, self.custom_frame]:
            for child in widget.winfo_children():
                if isinstance(child, (ctk.CTkSlider, ctk.CTkOptionMenu, ctk.CTkTextbox, ctk.CTkLabel)):
                    child.configure(text_color=("gray" if not enabled else None))
                if isinstance(child, (ctk.CTkSlider, ctk.CTkOptionMenu)):
                    child.configure(state=state)
                if isinstance(child, ctk.CTkTextbox):
                    child.configure(state=state)

    def _on_tts_backend_change(self, backend: str):
        """Обработчик выбора TTS движка."""
        lang = self.settings.ui_lang
        t = COMMENTS_TEXTS.get(lang, COMMENTS_TEXTS["ru"])
        # Описание обновляется автоматически при переключении

    def get_data(self) -> dict:
        """Сбор данных со страницы."""
        frequency = int(self.frequency_var.get())
        system_prompt = self.custom_prompt_text.get("1.0", "end-1c").strip()

        return {
            "comment_enabled": self.comments_enabled_var.get(),
            "comment_frequency": frequency,
            "system_prompt": system_prompt,
            "tts_backend": self.tts_backend_var.get(),
            "main_gender": self.main_gender_var.get(),
            "comment_gender": self.comment_gender_var.get(),
        }

    def validate(self) -> bool:
        """Валидация данных страницы."""
        try:
            freq = int(self.frequency_var.get())
            if freq < 1 or freq > 20:
                return False
        except ValueError:
            return False
        return True
