"""
Абстрактный интерфейс для движков синтеза речи.
Позволяет переключаться между Edge TTS (облачный) и Piper TTS (локальный).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional


class SynthesisCancelled(Exception):
    """Пользователь отменил синтез / создание аудиокниги."""


class TTSBackend(ABC):
    """Интерфейс движка синтеза речи.

    Все TTS-бэкенды (Edge, Piper и т.д.) реализуют этот интерфейс,
    что позволяет TTSManager выступать в роли фабрики.
    """

    @abstractmethod
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
            speed: Темп речи (1.0 = нормальный).
            output_dir: Директория для временного файла.

        Returns:
            Путь к синтезированному аудиофайлу.
        """
        ...

    @abstractmethod
    async def get_available_voices(self, lang: str) -> List[Dict[str, Any]]:
        """Получить список доступных голосов для языка.

        Args:
            lang: Код языка (ru, en, ja, zh).

        Returns:
            Список словарей с информацией о голосах.
            Каждый словарь содержит: name, locale, gender, friendly_name.
        """
        ...

    @abstractmethod
    async def close(self):
        """Освобождение ресурсов бэкенда."""
        ...
