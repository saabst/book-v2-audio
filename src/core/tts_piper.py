"""
Модуль локального синтеза речи через Piper TTS.
Не требует интернета после загрузки голосовых моделей.
"""

from __future__ import annotations

import asyncio
import logging
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx

from src.core.audio_bitrate import clamp_bitrate, ffmpeg_lame_bitrate_args
from src.core.tts_base import TTSBackend

logger = logging.getLogger(__name__)

# Доступные голоса Piper для каждого языка
# Формат: (piper_model_name, отображаемое_имя, пол)
# Актуальный список: https://huggingface.co/rhasspy/piper-voices/tree/main
PIPER_VOICES = {
    "ru": [
        ("ru_RU-irina-medium", "Ирина (среднее качество)", "female"),
        ("ru_RU-denis-medium", "Денис (среднее качество)", "male"),
        ("ru_RU-dmitri-medium", "Дмитрий (среднее качество)", "male"),
        ("ru_RU-ruslan-medium", "Руслан (среднее качество)", "male"),
    ],
    "en": [
        ("en_US-less-medium", "Less (среднее качество)", "female"),
        ("en_US-amy-medium", "Amy (среднее качество)", "female"),
        ("en_US-joe-medium", "Joe (среднее качество)", "male"),
        ("en_US-sam-medium", "Sam (среднее качество)", "male"),
        ("en_US-ryan-medium", "Ryan (среднее качество)", "male"),
        ("en_US-norman-medium", "Norman (среднее качество)", "male"),
        ("en_US-kristin-medium", "Kristin (среднее качество)", "female"),
        ("en_US-kusal-medium", "Kusal (среднее качество)", "male"),
    ],
    # Японские модели Piper отсутствуют на HuggingFace
    "zh": [
        ("zh_CN-chaowen-medium", "Chaowen (среднее качество)", "female"),
        ("zh_CN-huayan-medium", "Huayan (среднее качество)", "female"),
        ("zh_CN-xiao_ya-medium", "Xiao Ya (среднее качество)", "female"),
    ],
}

# Базовый URL для скачивания моделей Piper с HuggingFace
PIPER_HF_BASE = "https://huggingface.co/rhasspy/piper-voices/resolve/main"

# Директория кэша голосовых моделей
VOICE_CACHE_DIR = Path.home() / ".audiobook-generator" / "piper-voices"

# Маппинг языка → голос по умолчанию (main, comment)
DEFAULT_PIPER_VOICES = {
    "ru": ("ru_RU-irina-medium", "ru_RU-ruslan-medium"),
    "en": ("en_US-less-medium", "en_US-amy-medium"),
    "zh": ("zh_CN-chaowen-medium", "zh_CN-huayan-medium"),
}


class PiperTTSManager(TTSBackend):
    """Менеджер синтеза речи через Piper TTS (локальный).

    Использует Piper — лёгкий нейросетевой синтезатор на ONNX.
    Модели скачиваются с HuggingFace при первом использовании.
    """

    def __init__(self, config):
        """Инициализация PiperTTSManager.

        Args:
            config: Объект TTSConfig с настройками.
        """
        self.config = config
        self._voice_cache = VOICE_CACHE_DIR
        self._voice_cache.mkdir(parents=True, exist_ok=True)
        self._http_client: Optional[httpx.AsyncClient] = None

        # Проверяем доступность piper
        self._piper_path = self._find_piper()

        # Маппим Edge TTS голоса (из конфига) на Piper-голоса
        self._main_voice = self._resolve_piper_voice(config.main_voice)
        self._comment_voice = self._resolve_piper_voice(config.comment_voice, is_comment=True)

    def _find_piper(self) -> Optional[str]:
        """Поиск исполняемого файла piper в системе.

        Returns:
            Путь к piper или None, если не найден.
        """
        piper_cmd = shutil.which("piper")
        if piper_cmd:
            return piper_cmd

        # Проверяем в виртуальном окружении
        try:
            import sys
            venv_piper = str(Path(sys.executable).parent / "piper")
            if Path(venv_piper).exists():
                return venv_piper
        except Exception:
            pass

        return None

    def _resolve_piper_voice(self, voice_name: str, is_comment: bool = False) -> str:
        """Сопоставить имя голоса из конфига с голосом Piper.

        Если голос уже выглядит как Piper (underscore: ru_RU-irina-medium) —
        возвращаем как есть. Если это Edge TTS голос (дефис: ru-RU-SvetlanaNeural) —
        ищем подходящий голос Piper через DEFAULT_PIPER_VOICES.

        Args:
            voice_name: Имя голоса из конфига.
            is_comment: True если это голос комментатора.

        Returns:
            Имя модели Piper.
        """
        # Если голос уже похож на Piper — используем как есть
        if "_" in voice_name and any(
            voice_name.startswith(locale)
            for locale in ["ru_RU", "en_US", "en_GB", "ja_JP", "zh_CN"]
        ):
            return voice_name

        # Определяем язык по префиксу Edge TTS голоса
        edge_to_lang = {
            "ru-RU": "ru",
            "en-US": "en",
            "en-GB": "en",
            "ja-JP": "ja",
            "zh-CN": "zh",
            "zh-HK": "zh",
        }
        for prefix, lang in edge_to_lang.items():
            if voice_name.startswith(prefix):
                voices = DEFAULT_PIPER_VOICES.get(lang)
                if voices:
                    return voices[1] if is_comment else voices[0]

        # Не смогли определить — используем русские голоса по умолчанию
        logger.warning(
            "Не удалось определить Piper голос для '%s' (is_comment=%s), "
            "использую русский по умолчанию",
            voice_name, is_comment,
        )
        default = DEFAULT_PIPER_VOICES.get("ru", ("ru_RU-irina-medium", "ru_RU-denis-medium"))
        return default[1] if is_comment else default[0]

    async def _ensure_voice_model(self, voice: str) -> Path:
        """Загрузить голосовую модель Piper, если её нет в кэше.

        Модели Piper на HuggingFace организованы в иерархию:
            https://huggingface.co/rhasspy/piper-voices/resolve/main/
            {lang}/{locale}/{name}/{quality}/{locale}-{name}-{quality}.onnx

        Например для "ru_RU-irina-medium":
            .../ru/ru_RU/irina/medium/ru_RU-irina-medium.onnx

        Каждая модель состоит из двух файлов:
            {model_name}.onnx         — сама модель
            {model_name}.onnx.json    — конфигурация (не {model_name}.json!)

        Args:
            voice: Имя модели (например, "ru_RU-irina-medium").

        Returns:
            Путь к .onnx файлу модели.

        Raises:
            RuntimeError: Если не удалось загрузить модель.
        """
        model_name = voice
        onnx_path = self._voice_cache / f"{model_name}.onnx"
        json_path = self._voice_cache / f"{model_name}.onnx.json"

        # Если модель уже есть — возвращаем путь
        if onnx_path.exists() and json_path.exists():
            return onnx_path

        # Парсим имя модели для построения правильного URL на HuggingFace
        # Формат: {locale}-{name}-{quality} (например, ru_RU-irina-medium)
        parts = voice.rsplit("-", 2)
        if len(parts) == 3:
            locale_part, name_part, quality_part = parts
            lang_part = locale_part.split("_")[0]  # "ru" из "ru_RU"
            hf_path = f"{lang_part}/{locale_part}/{name_part}/{quality_part}"
        else:
            # Нестандартное имя — fallback на плоскую структуру
            logger.warning("Не удалось разобрать имя модели Piper: %s", voice)
            hf_path = model_name

        # Скачиваем модель
        logger.info("Загрузка голосовой модели Piper: %s...", model_name)
        if self._http_client is None:
            self._http_client = httpx.AsyncClient(timeout=120.0, follow_redirects=True)

        files_to_download = [
            (f"{model_name}.onnx", onnx_path),
            (f"{model_name}.onnx.json", json_path),
        ]

        for remote_name, local_path in files_to_download:
            url = f"{PIPER_HF_BASE}/{hf_path}/{remote_name}"
            try:
                response = await self._http_client.get(url)
                response.raise_for_status()
                with open(local_path, "wb") as f:
                    f.write(response.content)
                logger.debug("Загружено: %s (%d байт)", remote_name, len(response.content))
            except Exception as e:
                logger.error("Ошибка загрузки %s: %s", remote_name, e)
                # Удаляем частично загруженные файлы
                if local_path.exists():
                    local_path.unlink()
                if onnx_path.exists():
                    onnx_path.unlink()
                raise RuntimeError(
                    f"Не удалось загрузить голосовую модель Piper: {model_name}. "
                    f"Проверьте интернет-соединение."
                ) from e

        logger.info("Голосовая модель %s загружена успешно", model_name)
        return onnx_path

    async def _preload_models(self, voices: List[str]) -> None:
        """Предзагрузить несколько голосовых моделей параллельно.

        Загружает все указанные модели одновременно, чтобы избежать проблем
        с отсутствием модели комментатора в процессе синтеза.

        Args:
            voices: Список имён моделей (например, ["ru_RU-irina-medium", "ru_RU-denis-medium"]).
        """
        tasks = [self._ensure_voice_model(v) for v in set(voices)]
        if not tasks:
            return
        logger.info("Предзагрузка голосовых моделей Piper: %s", ", ".join(set(voices)))
        await asyncio.gather(*tasks)
        logger.debug("Все голосовые модели загружены")

    async def _run_piper(
        self,
        text: str,
        model_path: Path,
        output_path: Path,
    ) -> None:
        """Запуск Piper CLI для синтеза речи.

        Args:
            text: Текст для синтеза.
            model_path: Путь к .onnx модели.
            output_path: Путь для выходного WAV-файла.

        Raises:
            RuntimeError: Если piper не найден или синтез не удался.
        """
        if not self._piper_path:
            raise RuntimeError(
                "Piper TTS не найден. Установите: pip install piper-tts"
            )

        # Piper читает текст из stdin и пишет WAV в stdout
        # Используем `--output_file` для прямого сохранения
        cmd = [
            self._piper_path,
            "--model", str(model_path),
            "--output_file", str(output_path),
        ]

        try:
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            # communicate() не принимает timeout — оборачиваем через wait_for
            stdout, stderr = await asyncio.wait_for(
                process.communicate(input=text.encode("utf-8")),
                timeout=60.0,
            )

            if process.returncode != 0:
                error_msg = stderr.decode("utf-8", errors="replace") if stderr else ""
                raise RuntimeError(
                    f"Piper завершился с кодом {process.returncode}: {error_msg}"
                )

            # Проверяем, что WAV-файл содержит аудиоданные (не только заголовок)
            if output_path.exists() and output_path.stat().st_size <= 44:
                # WAV заголовок = 44 байта; если размер ≤44 — данных нет
                logger.warning(
                    "Piper создал пустой WAV (размер %d байт): %s",
                    output_path.stat().st_size, output_path,
                )
                output_path.unlink(missing_ok=True)
                raise RuntimeError(
                    "Piper не сгенерировал аудио — возможно, пустой или "
                    "неподдерживаемый текст"
                )

        except asyncio.TimeoutError:
            raise RuntimeError("Piper TTS превысил таймаут (60 сек)")

    def _mp3_codec_args(self) -> list:
        kbps = clamp_bitrate(
            getattr(self.config, "backend", "piper"),
            int(getattr(self.config, "audio_bitrate_kbps", 0) or 0),
        )
        return ffmpeg_lame_bitrate_args(kbps)

    async def _generate_silence_mp3(
        self, output_path: Path, duration_sec: float = 0.5
    ) -> None:
        """Сгенерировать тишину в MP3 через ffmpeg.

        Используется как fallback, когда Piper не может синтезировать
        пустой или неподдерживаемый текст.

        Args:
            output_path: Путь для выходного MP3-файла.
            duration_sec: Длительность тишины в секундах.

        Raises:
            RuntimeError: Если ffmpeg не удалось сгенерировать тишину.
        """
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
                f"Ошибка генерации тишины (код {process.returncode}): "
                f"{error_msg[:200]}"
            )

    async def _adjust_speed(self, wav_path: Path, speed: float, output_path: Path) -> None:
        """Изменить скорость аудио через ffmpeg atempo.

        Piper выводит WAV с частотой 22050 Гц.
        AudioAssembler использует 22050 Гц по умолчанию.
        Явно задаём `-ar 22050`, чтобы все MP3-фрагменты были
        в едином формате и concat-склейка не давала белого шума.

        Args:
            wav_path: Путь к исходному WAV-файлу.
            speed: Коэффициент скорости (1.0 = оригинал).
            output_path: Путь для выходного MP3-файла.

        Raises:
            RuntimeError: Если ffmpeg не удалось конвертировать WAV в MP3.
        """
        if abs(speed - 1.0) < 0.01:
            # Скорость не меняется — конвертируем WAV в MP3 с явной частотой
            cmd = [
                "ffmpeg", "-y", "-i", str(wav_path),
                "-ar", "22050",
                *self._mp3_codec_args(),
                str(output_path),
            ]
        else:
            # Меняем скорость через atempo
            # ffmpeg atempo поддерживает диапазон 0.5-2.0
            atempo = speed
            filter_str = f"atempo={atempo}"
            cmd = [
                "ffmpeg", "-y", "-i", str(wav_path),
                "-ar", "22050",
                "-filter:a", filter_str,
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
            logger.error(
                "ffmpeg speed adjustment failed (code %d): %s",
                process.returncode, error_msg,
            )
            raise RuntimeError(
                f"Ошибка ffmpeg при конвертации WAV→MP3 (код {process.returncode}): "
                f"{error_msg[:300]}"
            )

    async def synthesize_segment(
        self,
        text: str,
        voice: str,
        speed: float = 1.0,
        output_dir: Optional[Path] = None,
        segment_index: Optional[int] = None,
    ) -> Path:
        """Синтез одного текстового сегмента через Piper TTS.

        Если текст пуст или состоит только из пробелов — вместо вызова Piper
        генерируется короткая тишина (0.5 сек), чтобы не вызывать краш
        Piper TTS (wave.Error: # channels not specified) на пустом входе.

        Args:
            text: Текст для озвучки.
            voice: Имя голоса Piper (например, "ru_RU-irina-medium").
            speed: Темп речи (1.0 = нормальный).
            output_dir: Директория для временного файла.
            segment_index: Индекс сегмента для сортируемого имени файла.
                Если None — используется хеш (для обратной совместимости).

        Returns:
            Путь к MP3-файлу.

        Raises:
            RuntimeError: Если Piper не установлен или модель не загружена.
        """
        if not self._piper_path:
            raise RuntimeError(
                "Piper TTS не найден. Установите: pip install piper-tts\n"
                "Или переключитесь на Edge TTS в настройках."
            )

        if output_dir is None:
            output_dir = Path.cwd() / "temp_audio"
        output_dir.mkdir(parents=True, exist_ok=True)

        # Генерируем уникальное имя файла
        if segment_index is not None:
            # Индексное имя — гарантирует правильный порядок при сортировке
            seg_name = f"seg_{segment_index:06d}"
        else:
            import hashlib
            import time
            seg_name = hashlib.md5(f"{text}{time.time()}".encode()).hexdigest()[:8]
        wav_path = output_dir / f"{seg_name}.wav"
        mp3_path = output_dir / f"{seg_name}.mp3"

        # Проверка: если текст пустой, только из пробелов или не содержит
        # ни одной буквы (только цифры/пунктуация/тире) — Piper крашится
        # с wave.Error: # channels not specified, т.к. voice.synthesize()
        # не выдаёт ни одного аудио-чанка и WAV-заголовок не пишется.
        if not text or not text.strip() or not re.search(
            r'[а-яА-ЯёЁa-zA-Z]', text
        ):
            logger.warning(
                "Текст сегмента #%s не содержит букв — генерирую тишину (0.5с): %r",
                seg_name, text[:80],
            )
            await self._generate_silence_mp3(mp3_path, duration_sec=0.5)
            return mp3_path

        # Загружаем модель при необходимости
        model_path = await self._ensure_voice_model(voice)

        # Синтезируем через Piper
        await self._run_piper(text, model_path, wav_path)

        # Конвертируем WAV → MP3 с корректировкой скорости
        await self._adjust_speed(wav_path, speed, mp3_path)

        # Удаляем промежуточный WAV
        if wav_path.exists():
            wav_path.unlink()

        logger.debug(
            "Синтезирован сегмент (Piper): голос=%s, длина=%d символов, файл=%s",
            voice, len(text), mp3_path.name,
        )
        return mp3_path

    async def synthesize_chapter(
        self,
        text_segments: List[str],
        comment_segments: List[Optional[str]],
        chapter_dir: Path,
        progress_callback: Optional[callable] = None,
        detail_callback: Optional[callable] = None,
    ) -> Path:
        """Синтез целой главы через Piper TTS.

        Args:
            text_segments: Сегменты основного текста.
            comment_segments: Сегменты комментариев (None если нет).
            chapter_dir: Директория для временных файлов главы.
            progress_callback: Колбэк прогресса (текущий, всего).
            detail_callback: Колбэк с детальной информацией
                (текущий, всего, текст, голос, бэкенд).

        Returns:
            Путь к директории с аудиофайлами главы.
        """
        chapter_dir.mkdir(parents=True, exist_ok=True)

        # Предзагружаем обе модели (основную и комментатора) параллельно,
        # чтобы избежать ошибок при первом использовании голоса комментатора
        await self._preload_models([self._main_voice, self._comment_voice])

        total = len(text_segments) + len([c for c in comment_segments if c])
        completed = 0
        # Сквозной индекс для именования файлов — гарантирует правильный порядок
        # при сортировке (`sorted(glob())`) в _assemble_chapter_audio
        file_idx = 0

        def _report(seg_text: str, seg_voice: str):
            """Отправить детальную информацию о сегменте."""
            if detail_callback:
                preview = seg_text[:120].replace("\n", " ")
                detail_callback(completed + 1, total, preview, seg_voice, "piper")

        for i, text in enumerate(text_segments):
            # Основной текст — используем разрешённый Piper голос
            _report(text, self._main_voice)
            try:
                await self.synthesize_segment(
                    text, self._main_voice, self.config.main_speed, chapter_dir,
                    segment_index=file_idx,
                )
            except RuntimeError as e:
                # Safety net: если синтез сегмента упал (например, Piper
                # не смог обработать текст), генерируем тишину и продолжаем
                logger.error(
                    "Ошибка синтеза сегмента #%d (гл.текст): %s — генерирую тишину",
                    file_idx, e,
                )
                silence_path = chapter_dir / f"seg_{file_idx:06d}.mp3"
                await self._generate_silence_mp3(silence_path, duration_sec=0.5)
            file_idx += 1
            completed += 1
            if progress_callback:
                progress_callback(completed, total)

            # Комментарий (если есть для этого сегмента)
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
                    logger.error(
                        "Ошибка синтеза сегмента #%d (комментарий): %s — генерирую тишину",
                        file_idx, e,
                    )
                    silence_path = chapter_dir / f"seg_{file_idx:06d}.mp3"
                    await self._generate_silence_mp3(silence_path, duration_sec=0.5)
                file_idx += 1
                completed += 1
                if progress_callback:
                    progress_callback(completed, total)

        return chapter_dir

    async def get_available_voices(self, lang: str = "") -> List[Dict[str, Any]]:
        """Получение списка доступных голосов Piper.

        Args:
            lang: Код языка (ru, en, ja, zh). Если пусто — все голоса.

        Returns:
            Список словарей с информацией о голосах.
        """
        result = []
        for voice_lang, voices in PIPER_VOICES.items():
            if lang and voice_lang != lang:
                continue
            for model_name, friendly_name, gender in voices:
                result.append({
                    "name": model_name,
                    "locale": voice_lang,
                    "gender": gender,
                    "friendly_name": friendly_name,
                    "backend": "piper",
                })
        return result

    async def close(self):
        """Освобождение ресурсов."""
        if self._http_client is not None:
            await self._http_client.aclose()
            self._http_client = None

    @staticmethod
    def is_available() -> bool:
        """Проверка, установлен ли Piper TTS в системе.

        Returns:
            True если Piper доступен.
        """
        return shutil.which("piper") is not None

    @staticmethod
    def get_piper_voices_for_lang(lang: str) -> List[str]:
        """Получить список имён моделей Piper для языка.

        Args:
            lang: Код языка (ru, en, ja, zh).

        Returns:
            Список имён моделей (например, ["ru_RU-irina-medium", "ru_RU-denis-medium"]).
        """
        voices = PIPER_VOICES.get(lang, [])
        return [v[0] for v in voices]
