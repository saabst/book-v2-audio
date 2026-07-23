"""
Модуль локального синтеза речи через Supertonic 3 (Supertone Inc.).
Не требует интернета после загрузки голосовой модели (~305 МБ).

Голоса:
  M1-M5 — мужские (M1 — Порфирий для комментариев)
  F1-F5 — женские (F1 — озвучка книг)
Все голоса поддерживают русский и английский языки.
"""

from __future__ import annotations

import asyncio
import logging
import re
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.core.audio_bitrate import clamp_bitrate, ffmpeg_lame_bitrate_args
from src.core.tts_base import TTSBackend

logger = logging.getLogger(__name__)

# Доступные голоса Supertonic
SUPERTONIC_VOICES = {
    "ru": [
        ("F1", "Анна (высокое качество)", "female"),
        ("F2", "Елена (высокое качество)", "female"),
        ("F3", "Мария (высокое качество)", "female"),
        ("F4", "Ольга (высокое качество)", "female"),
        ("F5", "Татьяна (высокое качество)", "female"),
        ("M1", "Порфирий (высокое качество)", "male"),
        ("M2", "Александр (высокое качество)", "male"),
        ("M3", "Дмитрий (высокое качество)", "male"),
        ("M4", "Игорь (высокое качество)", "male"),
        ("M5", "Сергей (высокое качество)", "male"),
    ],
    "en": [
        ("F1", "F1 (high quality)", "female"),
        ("F2", "F2 (high quality)", "female"),
        ("F3", "F3 (high quality)", "female"),
        ("F4", "F4 (high quality)", "female"),
        ("F5", "F5 (high quality)", "female"),
        ("M1", "M1 (high quality)", "male"),
        ("M2", "M2 (high quality)", "male"),
        ("M3", "M3 (high quality)", "male"),
        ("M4", "M4 (high quality)", "male"),
        ("M5", "M5 (high quality)", "male"),
    ],
}

DEFAULT_SUPERTONIC_VOICES = {
    "ru": ("F1", "M1"),   # (озвучка, комментатор)
    "en": ("F1", "M1"),
}


class SupertonicTTSManager(TTSBackend):
    """Менеджер синтеза речи через Supertonic 3 (локальный).

    Использует on-device ONNX Runtime. Модель скачивается с HuggingFace
    при первом вызове synthesize_segment (~305 МБ).
    """

    _tts_instance = None
    _lock = asyncio.Lock()

    def __init__(self, config):
        self.config = config
        self._initialized = False

        # Маппим голоса из конфига
        self._main_voice = self._resolve_supertonic_voice(config.main_voice, is_comment=False)
        self._comment_voice = self._resolve_supertonic_voice(config.comment_voice, is_comment=True)

    async def _ensure_initialized(self):
        """Ленивая инициализация — первый вызов скачивает модель."""
        if self._initialized:
            return

        async with self._lock:
            if self._initialized:
                return
            try:
                from supertonic import TTS
                if SupertonicTTSManager._tts_instance is None:
                    logger.info("Supertonic: скачивание модели (~305 МБ) при первом запуске...")
                    SupertonicTTSManager._tts_instance = TTS(auto_download=True)
                self._tts = SupertonicTTSManager._tts_instance
                self._initialized = True
                logger.info("Supertonic TTS инициализирован")
            except ImportError:
                raise RuntimeError(
                    "Supertonic TTS не установлен. Выполните: pip install supertonic"
                )

    def _resolve_supertonic_voice(self, voice_name: str, is_comment: bool = False) -> str:
        """Сопоставить имя голоса из конфига с голосом Supertonic.

        Если голос выглядит как имя Supertonic (M1, F1 и т.д.) — возвращаем как есть.
        Если это Edge TTS голос — подставляем голос по умолчанию.
        """
        # Если голос уже похож на Supertonic
        if voice_name.upper() in {"M1", "M2", "M3", "M4", "M5", "F1", "F2", "F3", "F4", "F5"}:
            return voice_name.upper()

        # Определяем язык по префиксу Edge TTS голоса
        edge_to_lang = {"ru-RU": "ru", "en-US": "en", "en-GB": "en"}
        for prefix, lang in edge_to_lang.items():
            if voice_name.startswith(prefix):
                voices = DEFAULT_SUPERTONIC_VOICES.get(lang, DEFAULT_SUPERTONIC_VOICES["ru"])
                return voices[1] if is_comment else voices[0]

        logger.warning(
            "Не удалось определить Supertonic голос для '%s', использую умолчание",
            voice_name,
        )
        default = DEFAULT_SUPERTONIC_VOICES["ru"]
        return default[1] if is_comment else default[0]

    async def synthesize_segment(
        self,
        text: str,
        voice: str,
        speed: float = 1.0,
        output_dir: Optional[Path] = None,
        segment_index: Optional[int] = None,
    ) -> Path:
        """Синтез одного текстового сегмента через Supertonic.

        Args:
            text: Текст для озвучки.
            voice: Имя голоса (M1-M5, F1-F5).
            speed: Темп речи (1.0 = нормальный).
            output_dir: Директория для временного файла.
            segment_index: Индекс сегмента для сортируемого имени файла.

        Returns:
            Путь к MP3-файлу.
        """
        await self._ensure_initialized()

        if output_dir is None:
            output_dir = Path.cwd() / "temp_audio"
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        # Имя файла
        if segment_index is not None:
            seg_name = f"seg_{segment_index:06d}"
        else:
            import hashlib, time
            seg_name = hashlib.md5(f"{text}{time.time()}".encode()).hexdigest()[:8]

        wav_path = output_dir / f"{seg_name}.wav"
        mp3_path = output_dir / f"{seg_name}.mp3"

        # Пустой текст — тишина
        if not text or not text.strip():
            logger.warning(
                "Текст сегмента #%s пуст — генерирую тишину", seg_name
            )
            await self._generate_silence_mp3(mp3_path, duration_sec=0.5)
            return mp3_path

        # Определяем язык текста
        has_cyrillic = bool(re.search(r'[а-яА-ЯёЁ]', text))
        lang = "ru" if has_cyrillic else "en"

        # Нормализуем имя голоса
        voice_name = voice.upper()
        if voice_name not in {"M1", "M2", "M3", "M4", "M5", "F1", "F2", "F3", "F4", "F5"}:
            voice_name = self._resolve_supertonic_voice(voice, is_comment=False)

        # Синтезируем
        try:
            style = self._tts.get_voice_style(voice_name=voice_name)
            wav, _ = self._tts.synthesize(text, voice_style=style, lang=lang)
            self._tts.save_audio(wav, str(wav_path))
        except Exception as e:
            logger.error("Supertonic ошибка синтеза: %s", e)
            raise RuntimeError(f"Supertonic TTS ошибка: {e}") from e

        # Конвертируем WAV → MP3 с учётом скорости
        await self._adjust_speed(wav_path, speed, mp3_path)

        # Удаляем промежуточный WAV
        if wav_path.exists():
            wav_path.unlink()

        logger.debug(
            "Синтезирован сегмент (Supertonic): голос=%s, язык=%s, длина=%d символов",
            voice_name, lang, len(text),
        )
        return mp3_path

    def _mp3_codec_args(self) -> list:
        kbps = clamp_bitrate(
            getattr(self.config, "backend", "supertonic"),
            int(getattr(self.config, "audio_bitrate_kbps", 0) or 0),
        )
        return ffmpeg_lame_bitrate_args(kbps)

    async def _generate_silence_mp3(self, output_path: Path, duration_sec: float = 0.5):
        """Сгенерировать тишину через ffmpeg."""
        cmd = [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", "anullsrc=r=22050:cl=mono",
            "-t", str(duration_sec),
            *self._mp3_codec_args(),
            str(output_path),
        ]
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await process.communicate()
        if process.returncode != 0:
            error_msg = stderr.decode("utf-8", errors="replace") if stderr else ""
            raise RuntimeError(
                f"Ошибка генерации тишины (код {process.returncode}): {error_msg[:200]}"
            )

    async def _adjust_speed(self, wav_path: Path, speed: float, output_path: Path):
        """Изменить скорость аудио через ffmpeg atempo."""
        if abs(speed - 1.0) < 0.01:
            cmd = [
                "ffmpeg", "-y", "-i", str(wav_path),
                "-ar", "22050",
                *self._mp3_codec_args(),
                str(output_path),
            ]
        else:
            cmd = [
                "ffmpeg", "-y", "-i", str(wav_path),
                "-ar", "22050",
                "-filter:a", f"atempo={speed}",
                *self._mp3_codec_args(),
                str(output_path),
            ]

        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await process.communicate()
        if process.returncode != 0:
            error_msg = stderr.decode("utf-8", errors="replace") if stderr else ""
            raise RuntimeError(
                f"Ошибка ffmpeg (код {process.returncode}): {error_msg[:300]}"
            )

    async def synthesize_chapter(
        self,
        text_segments: List[str],
        comment_segments: List[Optional[str]],
        chapter_dir: Path,
        progress_callback=None,
        detail_callback=None,
    ) -> Path:
        """Синтез целой главы через Supertonic.

        Полностью аналогичен PiperTTSManager.synthesize_chapter по логике.
        """
        await self._ensure_initialized()
        chapter_dir.mkdir(parents=True, exist_ok=True)

        total = len(text_segments) + len([c for c in comment_segments if c])
        completed = 0
        file_idx = 0

        def _report(seg_text, seg_voice):
            if detail_callback:
                preview = seg_text[:120].replace("\n", " ")
                detail_callback(completed + 1, total, preview, seg_voice, "supertonic")

        for i, text in enumerate(text_segments):
            _report(text, self._main_voice)
            try:
                await self.synthesize_segment(
                    text, self._main_voice, self.config.main_speed, chapter_dir,
                    segment_index=file_idx,
                )
            except RuntimeError as e:
                logger.error("Ошибка сегмента #%d: %s — тишина", file_idx, e)
                silence_path = chapter_dir / f"seg_{file_idx:06d}.mp3"
                await self._generate_silence_mp3(silence_path, duration_sec=0.5)
            file_idx += 1
            completed += 1
            if progress_callback:
                progress_callback(completed, total)

            if i < len(comment_segments) and comment_segments[i]:
                comment = comment_segments[i]
                _report(comment, self._comment_voice)
                try:
                    await self.synthesize_segment(
                        comment, self._comment_voice,
                        self.config.comment_speed, chapter_dir,
                        segment_index=file_idx,
                    )
                except RuntimeError as e:
                    logger.error("Ошибка сегмента #%d (комм): %s — тишина", file_idx, e)
                    silence_path = chapter_dir / f"seg_{file_idx:06d}.mp3"
                    await self._generate_silence_mp3(silence_path, duration_sec=0.5)
                file_idx += 1
                completed += 1
                if progress_callback:
                    progress_callback(completed, total)

        return chapter_dir

    async def get_available_voices(self, lang: str = "") -> List[Dict[str, Any]]:
        """Список доступных голосов Supertonic."""
        result = []
        for voice_lang, voices in SUPERTONIC_VOICES.items():
            if lang and voice_lang != lang:
                continue
            for name, friendly_name, gender in voices:
                result.append({
                    "name": name,
                    "locale": voice_lang,
                    "gender": gender,
                    "friendly_name": friendly_name,
                    "backend": "supertonic",
                })
        return result

    async def close(self):
        """Освобождение ресурсов (не требуется для Supertonic)."""
        pass

    @staticmethod
    def is_available() -> bool:
        """Проверка, установлен ли Supertonic TTS."""
        try:
            import supertonic  # noqa: F401
            return True
        except ImportError:
            return False
