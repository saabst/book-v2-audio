"""
Модуль управления настройками приложения.
Загрузка и сохранение конфигурации в TOML-файл.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional

import tomllib as tomli
import tomli_w

logger = logging.getLogger(__name__)

# Путь по умолчанию для файла настроек
DEFAULT_CONFIG_DIR = Path.home() / ".audiobook-generator"
DEFAULT_CONFIG_PATH = DEFAULT_CONFIG_DIR / "settings.toml"


@dataclass
class Settings:
    """Настройки приложения."""
    # Язык
    ui_lang: str = "ru"
    book_lang: str = "ru"

    # AI-провайдер
    ai_provider: str = "deepseek"

    # TTS бэкенд
    tts_backend: str = "edge"  # "edge" | "piper" | "supertonic" | "silero"
    # Битрейт MP3 (kbps); для Edge всегда 48
    tts_bitrate_kbps: int = 48

    # Пол голосов TTS (резолвятся в имена через движок + язык книги)
    main_gender: str = "female"
    comment_gender: str = "female"
    main_speed: float = 1.0
    comment_speed: float = 1.0

    # Паузы
    pause_before_comment: float = 1.0
    pause_after_comment: float = 0.7
    pause_between_sentences: float = 0.3
    chapter_pause: float = 1.5

    # Комментарии
    comment_enabled: bool = True
    comment_frequency: int = 5
    max_concurrent: int = 5
    system_prompt: str = ""

    # Пути
    output_dir: str = str(Path.home() / "audiobooks")
    book_path: str = ""

    # Окно
    window_width: int = 800
    window_height: int = 600

    # Первый запуск
    first_run: bool = True

    # Диапазон глав (0 = все)
    chapter_start: int = 0
    chapter_end: int = 0


# Определение пола по имени голоса (для миграции со старых версий)
_KNOWN_FEMALE_VOICES = {
    "ru-RU-SvetlanaNeural", "ru-RU-DariyaNeural",
    "en-US-JennyNeural", "en-GB-SoniaNeural",
    "ja-JP-NanamiNeural",
    "zh-CN-XiaoxiaoNeural",
}
_KNOWN_MALE_VOICES = {
    "ru-RU-DmitryNeural", "ru-RU-MaxNeural",
    "en-US-GuyNeural", "en-GB-RyanNeural",
    "ja-JP-KeitaNeural",
    "zh-CN-YunxiNeural",
}


def _infer_gender_from_voice(voice_name: str) -> str:
    """Определить пол по имени голоса (для миграции)."""
    if voice_name in _KNOWN_FEMALE_VOICES:
        return "female"
    if voice_name in _KNOWN_MALE_VOICES:
        return "male"
    if "Female" in voice_name or "female" in voice_name:
        return "female"
    if "Male" in voice_name or "male" in voice_name:
        return "male"
    # Edge: суффикс Neural — пытаемся угадать по первой букве имени
    # Svetlana, Jenny, Nanami — женские; Dmitry, Guy, Keita — мужские
    return "female"  # fallback


def _migrate_voices(settings: dict) -> dict:
    """Миграция старых настроек: main_voice/comment_voice → main_gender/comment_gender.

    Args:
        settings: Сырой словарь из TOML-файла (ещё не превращён в Settings).

    Returns:
        Обновлённый словарь без полей main_voice/comment_voice.
    """
    if "main_voice" in settings and "main_gender" not in settings:
        old_voice = settings.pop("main_voice")
        gender = _infer_gender_from_voice(old_voice)
        settings["main_gender"] = gender
        logger.info("Миграция main_voice='%s' → main_gender='%s'", old_voice, gender)

    if "comment_voice" in settings and "comment_gender" not in settings:
        old_voice = settings.pop("comment_voice")
        gender = _infer_gender_from_voice(old_voice)
        settings["comment_gender"] = gender
        logger.info("Миграция comment_voice='%s' → comment_gender='%s'", old_voice, gender)

    return settings


def load_settings(config_path: Optional[Path] = None) -> Settings:
    """Загрузка настроек из TOML-файла.

    Args:
        config_path: Путь к файлу настроек. По умолчанию ~/.audiobook-generator/settings.toml.

    Returns:
        Объект Settings с загруженными настройками.
    """
    path = config_path or DEFAULT_CONFIG_PATH

    if not path.exists():
        logger.info("Файл настроек не найден: %s, используются настройки по умолчанию", path)
        return Settings()

    try:
        with open(path, "rb") as f:
            data = tomli.load(f)

        # Мигрируем старые поля (main_voice/comment_voice → main_gender/comment_gender)
        data = _migrate_voices(data)

        # Фильтруем только известные поля
        known_fields = {k for k in asdict(Settings())}
        filtered_data = {k: v for k, v in data.items() if k in known_fields}

        settings = Settings(**filtered_data)
        # Битрейт должен быть допустим для выбранного движка
        from src.core.audio_bitrate import clamp_bitrate
        settings.tts_bitrate_kbps = clamp_bitrate(
            settings.tts_backend, int(settings.tts_bitrate_kbps or 0),
        )
        logger.info("Настройки загружены: %s", path)
        return settings

    except (tomli.TOMLDecodeError, IOError, TypeError) as e:
        logger.warning("Ошибка загрузки настроек: %s", e)
        return Settings()


def save_settings(settings: Settings, config_path: Optional[Path] = None) -> None:
    """Сохранение настроек в TOML-файл.

    Args:
        settings: Объект Settings для сохранения.
        config_path: Путь к файлу настроек. По умолчанию ~/.audiobook-generator/settings.toml.
    """
    path = config_path or DEFAULT_CONFIG_PATH
    path.parent.mkdir(parents=True, exist_ok=True)

    data = asdict(settings)

    try:
        with open(path, "wb") as f:
            tomli_w.dump(data, f)
        logger.info("Настройки сохранены: %s", path)
    except (IOError, TypeError) as e:
        logger.error("Ошибка сохранения настроек: %s", e)
