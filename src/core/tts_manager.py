"""
Модуль управления синтезом речи.
Содержит TTSConfig и TTSManager — фабрику/диспетчер для выбора бэкенда.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from src.core.tts_base import SynthesisCancelled, TTSBackend

logger = logging.getLogger(__name__)


# Маппинг (движок, язык, пол) → имя голоса
BACKEND_VOICES = {
    "edge": {
        "ru": {"male": "ru-RU-DmitryNeural", "female": "ru-RU-SvetlanaNeural"},
        "en": {"male": "en-US-GuyNeural", "female": "en-US-JennyNeural"},
        "ja": {"male": "ja-JP-KeitaNeural", "female": "ja-JP-NanamiNeural"},
        "zh": {"male": "zh-CN-YunxiNeural", "female": "zh-CN-XiaoxiaoNeural"},
    },
    "silero": {
        "ru": {"male": "eugene", "female": "xenia"},
        "en": {"male": "random", "female": "lj_16khz"},
    },
    "piper": {
        "ru": {"male": "ru_RU-dmitri-medium", "female": "ru_RU-irina-medium"},
        "en": {"male": "en_US-joe-medium", "female": "en_US-amy-medium"},
        # Для китайского в Piper только женские голоса
        "zh": {"male": "zh_CN-xiao_ya-medium", "female": "zh_CN-xiao_ya-medium"},
    },
    "supertonic": {
        "ru": {"male": "M1", "female": "F1"},
        "en": {"male": "M1", "female": "F1"},
        "ja": {"male": "M1", "female": "F1"},
        "zh": {"male": "M1", "female": "F1"},
    },
}
# Fallback: если язык не найден — берём английский
_FALLBACK_LANG = "en"


def resolve_voice(backend: str, book_lang: str, gender: str) -> str:
    """Преобразовать (движок, язык книги, пол) в конкретное имя голоса.

    Args:
        backend: Имя TTS-движка ("edge", "silero", "piper", "supertonic").
        book_lang: Код языка книги ("ru", "en", "ja", "zh").
        gender: Пол ("male" или "female").

    Returns:
        Имя голоса (например, "ru-RU-DmitryNeural" или "eugene").
    """
    voices_for_backend = BACKEND_VOICES.get(backend, BACKEND_VOICES["edge"])
    voices_for_lang = voices_for_backend.get(book_lang, voices_for_backend.get(_FALLBACK_LANG, {}))
    return voices_for_lang.get(gender, voices_for_lang.get("female", next(iter(voices_for_lang.values()))))


@dataclass
class TTSConfig:
    """Конфигурация синтеза речи."""
    backend: str = "edge"  # "edge" | "piper"
    main_voice: str = "ru-RU-SvetlanaNeural"
    comment_voice: str = "ru-RU-DmitryNeural"
    main_speed: float = 1.0  # 1.0 = нормальный темп
    comment_speed: float = 1.0
    pause_before_comment: float = 1.0  # секунд тишины перед комментарием
    pause_after_comment: float = 0.7  # секунд тишины после комментария
    pause_between_sentences: float = 0.3  # пауза между предложениями


# Словарь для обратной связи backend → читаемое название
BACKEND_NAMES = {
    "edge": "Edge TTS",
    "piper": "Piper (локальный)",
    "supertonic": "Supertonic 3 (локальный)",
    "silero": "Silero TTS (локальный)",
}


class TTSManager:
    """Фабрика/диспетчер для TTS-бэкендов.

    Создаёт нужный бэкенд (EdgeTTSManager, PiperTTSManager) на основе
    config.backend и делегирует ему все вызовы.
    """

    def __init__(self, config: TTSConfig):
        self.config = config
        self._backend: Optional[TTSBackend] = None
        self._cancel_event: Optional[threading.Event] = None

    def bind_cancel_event(self, event: Optional[threading.Event]) -> None:
        """Привязать событие отмены (проверяется между сегментами и retry)."""
        self._cancel_event = event

    def raise_if_canceled(self) -> None:
        if self._cancel_event is not None and self._cancel_event.is_set():
            raise SynthesisCancelled("Создание аудиокниги отменено")

    async def _get_backend(self) -> TTSBackend:
        """Ленивая инициализация бэкенда."""
        if self._backend is not None:
            if hasattr(self._backend, "bind_cancel_event"):
                self._backend.bind_cancel_event(self._cancel_event)
            return self._backend

        if self.config.backend == "edge":
            from src.core.tts_edge import EdgeTTSManager
            self._backend = EdgeTTSManager(self.config)
        elif self.config.backend == "piper":
            from src.core.tts_piper import PiperTTSManager
            self._backend = PiperTTSManager(self.config)
        elif self.config.backend == "supertonic":
            from src.core.tts_supertonic import SupertonicTTSManager
            self._backend = SupertonicTTSManager(self.config)
        elif self.config.backend == "silero":
            from src.core.tts_silero import SileroTTSManager
            self._backend = SileroTTSManager(self.config)
        else:
            raise ValueError(f"Неизвестный TTS-бэкенд: {self.config.backend}")

        if hasattr(self._backend, "bind_cancel_event"):
            self._backend.bind_cancel_event(self._cancel_event)

        logger.info("TTS бэкенд: %s", BACKEND_NAMES.get(self.config.backend, self.config.backend))
        return self._backend

    async def synthesize_segment(
        self,
        text: str,
        voice: str,
        speed: float = 1.0,
        output_dir: Optional[Path] = None,
    ) -> Path:
        """Синтез одного текстового сегмента в аудиофайл.

        Args:
            text: Текст для озвучки.
            voice: Имя голоса.
            speed: Темп речи.
            output_dir: Директория для временного файла.

        Returns:
            Путь к аудиофайлу.
        """
        backend = await self._get_backend()
        return await backend.synthesize_segment(text, voice, speed, output_dir)

    async def synthesize_chapter(
        self,
        text_segments: List[str],
        comment_segments: List[Optional[str]],
        chapter_dir: Path,
        progress_callback: Optional[Callable[[int, int], None]] = None,
        detail_callback: Optional[Callable[[int, int, str, str, str], None]] = None,
    ) -> Path:
        """Синтез целой главы с комментариями.

        Args:
            text_segments: Сегменты основного текста.
            comment_segments: Сегменты комментариев (None если нет).
            chapter_dir: Директория для временных файлов главы.
            progress_callback: Колбэк прогресса (текущий, всего).
            detail_callback: Колбэк с деталями (текущий, всего, текст, голос, бэкенд).

        Returns:
            Путь к директории с аудиофайлами главы.
        """
        backend = await self._get_backend()

        # EdgeTTSManager имеет detail_callback, PiperTTSManager — нет
        if hasattr(backend, 'synthesize_chapter'):
            # Пробуем передать detail_callback, если бэкенд его поддерживает
            import inspect
            sig = inspect.signature(backend.synthesize_chapter)
            if 'detail_callback' in sig.parameters:
                return await backend.synthesize_chapter(
                    text_segments, comment_segments, chapter_dir,
                    progress_callback=progress_callback,
                    detail_callback=detail_callback,
                )

        return await backend.synthesize_chapter(
            text_segments, comment_segments, chapter_dir,
            progress_callback=progress_callback,
        )

    async def get_available_voices(self, lang: str = "") -> List[Dict[str, Any]]:
        """Получение списка доступных голосов.

        Args:
            lang: Код языка для фильтрации (например, "ru"). Если пусто — все голоса.

        Returns:
            Список словарей с информацией о голосах.
        """
        backend = await self._get_backend()
        return await backend.get_available_voices(lang)

    async def close(self):
        """Освобождение ресурсов бэкенда."""
        if self._backend is not None:
            await self._backend.close()
