"""
Модуль локального синтеза речи через Silero TTS v5.
Не требует интернета после загрузки модели (~150 МБ).

Голоса для русского:
  xenia  — женский
  eugene — мужской

Особенности:
  - Лучшее качество русского среди open-source TTS
  - Автоматические ударения и поддержка омографов
  - Работает на CPU (PyTorch)
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

# Доступные голоса Silero
SILERO_VOICES = {
    "ru": [
        ("xenia", "Ксения (женский)", "female"),
        ("eugene", "Евгений (мужской)", "male"),
        ("random", "Случайный", "male"),
    ],
    "en": [
        ("lj_16khz", "LJ Speech (женский)", "female"),
        ("random", "Random", "male"),
    ],
}

DEFAULT_SILERO_VOICES = {
    "ru": ("xenia", "eugene"),   # (озвучка, комментатор)
    "en": ("lj_16khz", "random"),
}

# Маппинг Edge TTS префиксов → язык Silero
EDGE_PREFIX_TO_LANG = {
    "ru-RU": "ru",
    "en-US": "en",
    "en-GB": "en",
}


class SileroTTSManager(TTSBackend):
    """Менеджер синтеза речи через Silero TTS v5 (локальный).

    Модель загружается с PyTorch Hub при первом вызове synthesize_segment
    и кэшируется в ~/.cache/torch/hub/ (~150 МБ).
    """

    _model_instance = None
    _model_lock = asyncio.Lock()

    def __init__(self, config):
        self.config = config
        self._initialized = False
        self._tts_main = None   # SileroTTS для русского текста (основной голос)
        self._tts_comment = None  # SileroTTS для русского текста (комментатор)
        self._tts_en_main = None  # SileroTTS для английского текста
        self._sample_rate = 48000

        # Маппим голоса из конфига
        self._main_voice = self._resolve_silero_voice(config.main_voice, is_comment=False)
        self._comment_voice = self._resolve_silero_voice(config.comment_voice, is_comment=True)

    def _has_cyrillic(self, text: str) -> bool:
        """Проверка, есть ли в тексте кириллица."""
        import re
        return bool(re.search(r'[а-яА-ЯёЁ]', text))

    async def _ensure_initialized(self):
        """Ленивая инициализация — загрузка моделей при первом вызове."""
        if self._initialized:
            return

        async with self._model_lock:
            if self._initialized:
                return

            try:
                from silero_tts.silero_tts import SileroTTS

                logger.info("Silero: загрузка русской модели v5_ru (~150 МБ)...")
                self._tts_main = SileroTTS(
                    model_id="v5_ru",
                    language="ru",
                    speaker=self._main_voice,
                    sample_rate=self._sample_rate,
                    device="cpu",
                )

                if self._comment_voice != self._main_voice:
                    self._tts_comment = SileroTTS(
                        model_id="v5_ru",
                        language="ru",
                        speaker=self._comment_voice,
                        sample_rate=self._sample_rate,
                        device="cpu",
                    )
                else:
                    self._tts_comment = self._tts_main

                logger.info("Silero: загрузка английской модели lj_16khz...")
                try:
                    self._tts_en_main = SileroTTS(
                        model_id="v3_en",
                        language="en",
                        speaker="lj_16khz",
                        sample_rate=self._sample_rate,
                        device="cpu",
                    )
                    logger.info("Silero: английская модель загружена (lj_16khz)")
                except Exception as e:
                    logger.warning("Silero: не удалось загрузить английскую модель: %s", e)
                    self._tts_en_main = None

                self._initialized = True
                logger.info("Silero TTS v5 инициализирован")
            except Exception as e:
                raise RuntimeError(
                    f"Ошибка загрузки Silero TTS: {e}. "
                    f"Убедитесь, что установлен PyTorch: "
                    f"pip install torch torchaudio --index-url https://download.pytorch.org/whl/cpu"
                ) from e
    def _resolve_silero_voice(self, voice_name: str, is_comment: bool = False) -> str:
        """Сопоставить имя голоса из конфига с голосом Silero.

        Если голос уже похож на Silero (xenia, eugene и т.д.) — возвращаем как есть.
        Если это Edge TTS голос — подставляем голос по умолчанию.
        """
        # Если голос уже Silero
        known_voices = {"xenia", "eugene", "random", "lj_16khz"}
        if voice_name.lower() in known_voices:
            return voice_name.lower()

        # Определяем язык по префиксу Edge TTS голоса
        for prefix, lang in EDGE_PREFIX_TO_LANG.items():
            if voice_name.startswith(prefix):
                voices = DEFAULT_SILERO_VOICES.get(lang, DEFAULT_SILERO_VOICES["ru"])
                return voices[1] if is_comment else voices[0]

        logger.warning(
            "Не удалось определить Silero голос для '%s', использую умолчание",
            voice_name,
        )
        default = DEFAULT_SILERO_VOICES["ru"]
        return default[1] if is_comment else default[0]

    def _detect_lang(self, text: str) -> str:
        """Определение языка текста по наличию кириллицы."""
        has_cyrillic = bool(re.search(r'[а-яА-ЯёЁ]', text))
        return "ru" if has_cyrillic else "en"

    async def synthesize_segment(
        self,
        text: str,
        voice: str,
        speed: float = 1.0,
        output_dir: Optional[Path] = None,
        segment_index: Optional[int] = None,
    ) -> Path:
        """Синтез одного текстового сегмента через Silero.

        Args:
            text: Текст для озвучки.
            voice: Имя голоса (xenia, eugene, random).
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
            import hashlib
            import time
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

        # Определяем язык
        lang = self._detect_lang(text)

        # Нормализуем имя голоса
        voice_name = voice.lower()
        known_voices = {"xenia", "eugene", "random", "lj_16khz"}
        if voice_name not in known_voices:
            voice_name = self._resolve_silero_voice(voice, is_comment=False)

        # Синтезируем
        try:
            # Выбираем модель по языку текста
            if self._has_cyrillic(text):
                if voice_name == self._comment_voice and self._tts_comment is not None:
                    tts = self._tts_comment
                else:
                    tts = self._tts_main
            else:
                # Английский текст — используем английскую модель
                if self._tts_en_main is not None:
                    tts = self._tts_en_main
                else:
                    # Английской модели нет — тишина
                    logger.warning(
                        "Silero: нет английской модели для текста '%s...' — тишина",
                        text[:60],
                    )
                    await self._generate_silence_mp3(mp3_path, duration_sec=0.5)
                    return mp3_path

            # SileroTTS.tts() записывает WAV напрямую
            tts.tts(text, str(wav_path))

            # Проверяем, что WAV не пустой (silero может "успешно" сохранить пустышку
            # для неподдерживаемого языка или символов)
            if not wav_path.exists() or wav_path.stat().st_size < 1000:
                logger.warning(
                    "Silero: синтез вернул пустой WAV для текста '%s...' — генерирую тишину",
                    text[:60],
                )
                wav_path.unlink(missing_ok=True)
                await self._generate_silence_mp3(mp3_path, duration_sec=0.5)
                return mp3_path

        except Exception as e:
            logger.error("Silero ошибка синтеза: %s", e)
            raise RuntimeError(f"Silero TTS ошибка: {e}") from e

        # Конвертируем WAV → MP3 с учётом скорости
        await self._adjust_speed(wav_path, speed, mp3_path)

        # Удаляем промежуточный WAV
        if wav_path.exists():
            wav_path.unlink()

        logger.debug(
            "Синтезирован сегмент (Silero): голос=%s, язык=%s, длина=%d символов",
            voice_name, lang, len(text),
        )
        return mp3_path

    def _mp3_codec_args(self) -> list:
        kbps = clamp_bitrate(
            getattr(self.config, "backend", "silero"),
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
        """Синтез целой главы через Silero.

        Полностью аналогичен SupertonicTTSManager.synthesize_chapter по логике.
        """
        await self._ensure_initialized()
        chapter_dir.mkdir(parents=True, exist_ok=True)

        total = len(text_segments) + len([c for c in comment_segments if c])
        completed = 0
        file_idx = 0

        def _report(seg_text, seg_voice):
            if detail_callback:
                preview = seg_text[:120].replace("\n", " ")
                detail_callback(completed + 1, total, preview, seg_voice, "silero")

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
        """Список доступных голосов Silero."""
        result = []
        for voice_lang, voices in SILERO_VOICES.items():
            if lang and voice_lang != lang:
                continue
            for name, friendly_name, gender in voices:
                result.append({
                    "name": name,
                    "locale": voice_lang,
                    "gender": gender,
                    "friendly_name": friendly_name,
                    "backend": "silero",
                })
        return result

    async def close(self):
        """Освобождение ресурсов (не требуется для Silero)."""
        pass

    @staticmethod
    def is_available() -> bool:
        """Проверка, установлен ли Silero TTS (PyTorch + silero-models)."""
        try:
            import torch  # noqa: F401
            return True
        except ImportError:
            return False
