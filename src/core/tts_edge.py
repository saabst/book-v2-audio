"""
Модуль синтеза речи через Microsoft Edge TTS.
Реализует интерфейс TTSBackend.
"""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import edge_tts

from src.core.tts_base import SynthesisCancelled, TTSBackend

logger = logging.getLogger(__name__)

# Отдельный файл с провалившимися сегментами (текст целиком)
_FAILURES_LOG = Path.home() / ".audiobook-generator" / "edge_tts_failures.log"


def _log_edge_failure(
    *,
    voice: str,
    rate: str,
    segment_index: Optional[int],
    attempt: int,
    max_retries: int,
    exc: BaseException,
    text: str,
    final: bool = False,
) -> None:
    """Пишет детали сбоя Edge TTS в основной лог и в failures-файл."""
    preview = text[:200].replace("\n", " ")
    idx = segment_index if segment_index is not None else -1
    level = logger.error if final else logger.warning
    level(
        "Edge TTS %s: seg=%s attempt=%d/%d voice=%s rate=%s chars=%d err=%s text=%r",
        "FAIL" if final else "retry",
        idx,
        attempt,
        max_retries,
        voice,
        rate,
        len(text),
        f"{type(exc).__name__}: {exc}",
        preview,
    )
    try:
        _FAILURES_LOG.parent.mkdir(parents=True, exist_ok=True)
        with _FAILURES_LOG.open("a", encoding="utf-8") as fh:
            fh.write(
                f"\n--- {datetime.now().isoformat(timespec='seconds')} "
                f"{'FINAL' if final else 'retry'} seg={idx} "
                f"attempt={attempt}/{max_retries} voice={voice} rate={rate} "
                f"chars={len(text)} err={type(exc).__name__}: {exc}\n"
            )
            fh.write(text)
            fh.write("\n")
    except OSError as log_exc:
        logger.warning("Не удалось записать edge_tts_failures.log: %s", log_exc)


# Голоса по умолчанию для разных языков
DEFAULT_VOICES = {
    "ru": {
        "main": "ru-RU-SvetlanaNeural",
        "comment": "ru-RU-DmitryNeural",
    },
    "en": {
        "main": "en-US-JennyNeural",
        "comment": "en-US-GuyNeural",
    },
    "ja": {
        "main": "ja-JP-NanamiNeural",
        "comment": "ja-JP-KeitaNeural",
    },
    "zh": {
        "main": "zh-CN-XiaoxiaoNeural",
        "comment": "zh-CN-YunxiNeural",
    },
}


class EdgeTTSManager(TTSBackend):
    """Менеджер синтеза речи через edge-tts.

    Поддерживает два раздельных голоса: для основного текста и для комментариев.
    """

    def __init__(self, config):
        """Инициализация EdgeTTSManager.

        Args:
            config: Объект TTSConfig с настройками (main_voice, comment_voice, main_speed, comment_speed).
        """
        self.config = config
        # Сохраняем голоса для быстрого доступа из колбэков
        self._main_voice_name = ""
        self._comment_voice_name = ""
        self._cancel_event = None

    def bind_cancel_event(self, event) -> None:
        self._cancel_event = event

    def _raise_if_canceled(self) -> None:
        if self._cancel_event is not None and self._cancel_event.is_set():
            raise SynthesisCancelled("Создание аудиокниги отменено")

    async def synthesize_segment(
        self,
        text: str,
        voice: str,
        speed: float = 1.0,
        output_dir: Optional[Path] = None,
        max_retries: int = 5,
        retry_delay: float = 2.0,
        segment_index: Optional[int] = None,
    ) -> Path:
        """Синтез одного текстового сегмента в аудиофайл с повторными попытками.

        Args:
            text: Текст для озвучки.
            voice: Имя голоса (например, ru-RU-DariyaNeural).
            speed: Темп речи (1.0 = нормальный).
            output_dir: Директория для временного файла.
            max_retries: Количество повторных попыток при ошибках сервера (5xx).
            retry_delay: Начальная задержка перед повтором (сек), удваивается.
            segment_index: Индекс сегмента (для именования файлов).

        Returns:
            Путь к аудиофайлу.

        Raises:
            RuntimeError: Если все попытки исчерпаны или текст пустой.
        """
        if output_dir is None:
            output_dir = Path.cwd() / "temp_audio"
        output_dir.mkdir(parents=True, exist_ok=True)

        # Проверка текста: пустой, только пробелы или без букв — генерируем тишину
        if not text or not text.strip() or not re.search(
            r'[а-яА-ЯёЁa-zA-Z]', text
        ):
            logger.warning(
                "Текст сегмента не содержит букв — генерирую тишину (0.5с): %r",
                text[:80],
            )
            seg_name = f"seg_{segment_index:06d}" if segment_index is not None else "silence"
            output_path = output_dir / f"{seg_name}.mp3"
            await self._generate_silence_mp3(output_path, duration_sec=0.5)
            return output_path

        # Именование по индексу (если есть) или по хешу
        if segment_index is not None:
            output_path = output_dir / f"seg_{segment_index:06d}.mp3"
        else:
            import hashlib
            import time
            hash_str = hashlib.md5(f"{text}{time.time()}".encode()).hexdigest()[:8]
            output_path = output_dir / f"segment_{hash_str}.mp3"

        # Настройка скорости через SSML
        rate = f"+{int((speed - 1.0) * 100)}%" if speed >= 1.0 else f"-{int((1.0 - speed) * 100)}%"

        # Таймаут на синтез одного сегмента (сек).
        SEGMENT_TIMEOUT = 120

        last_exception: Optional[Exception] = None
        for attempt in range(1, max_retries + 1):
            self._raise_if_canceled()
            try:
                communicate = edge_tts.Communicate(text, voice, rate=rate)
                await asyncio.wait_for(
                    communicate.save(str(output_path)),
                    timeout=SEGMENT_TIMEOUT,
                )

                if attempt > 1:
                    logger.info(
                        "Edge TTS OK после retry: seg=%s attempt=%d voice=%s chars=%d file=%s",
                        segment_index if segment_index is not None else -1,
                        attempt,
                        voice,
                        len(text),
                        output_path.name,
                    )
                else:
                    logger.debug(
                        "Синтезирован сегмент: голос=%s, длина=%d символов, файл=%s",
                        voice, len(text), output_path.name,
                    )
                return output_path

            except SynthesisCancelled:
                raise
            except Exception as exc:
                last_exception = exc
                exc_str = str(exc)

                # Повторяемые ошибки: 5xx сервера + временные DNS/сетевые сбои
                # + NoAudioReceived (Edge часто отваливается на отдельных фразах)
                retryable_patterns = [
                    "503", "502", "500",           # Server errors
                    "Temporary failure in name resolution",  # DNS
                    "Name or service not known",   # DNS
                    "Cannot connect to host",      # Network
                    "Connection refused",          # Network
                    "Connection reset",            # Network
                    "Timeout", "timed out",        # Timeout
                    "No audio was received",       # Edge TTS flake / rate-limit
                ]
                is_retryable = (
                    any(p in exc_str for p in retryable_patterns)
                    or isinstance(exc, (TimeoutError, asyncio.TimeoutError))
                    or type(exc).__name__ == "NoAudioReceived"
                )

                if is_retryable and attempt < max_retries:
                    self._raise_if_canceled()
                    delay = retry_delay * (2 ** (attempt - 1))
                    _log_edge_failure(
                        voice=voice,
                        rate=rate,
                        segment_index=segment_index,
                        attempt=attempt,
                        max_retries=max_retries,
                        exc=exc,
                        text=text,
                        final=False,
                    )
                    logger.warning("Повтор Edge TTS через %.1f сек...", delay)
                    # Прерываемый sleep: проверяем отмену каждую секунду
                    remaining = delay
                    while remaining > 0:
                        self._raise_if_canceled()
                        step = min(0.25, remaining)
                        await asyncio.sleep(step)
                        remaining -= step
                    continue

                # Финальный провал: логируем полный текст и бросаем RuntimeError,
                # чтобы synthesize_chapter мог подставить тишину и продолжить книгу.
                _log_edge_failure(
                    voice=voice,
                    rate=rate,
                    segment_index=segment_index,
                    attempt=attempt,
                    max_retries=max_retries,
                    exc=exc,
                    text=text,
                    final=True,
                )
                raise RuntimeError(
                    f"Edge TTS: seg={segment_index} voice={voice} "
                    f"chars={len(text)} после {attempt}/{max_retries}: {exc_str}"
                ) from exc

        # Все попытки исчерпаны
        raise RuntimeError(
            f"Edge TTS: все {max_retries} попыток исчерпаны: {last_exception}"
        ) from last_exception

    async def _generate_silence_mp3(
        self, output_path: Path, duration_sec: float = 0.5
    ) -> None:
        """Сгенерировать тишину в MP3 через ffmpeg.

        Используется как fallback, когда Edge TTS не может синтезировать
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
            "-acodec", "libmp3lame", "-q:a", "2",
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
            detail_callback: Колбэк с детальной информацией (текущий, всего, текст, голос, бэкенд).

        Returns:
            Путь к директории с аудиофайлами главы.
        """
        chapter_dir.mkdir(parents=True, exist_ok=True)
        audio_paths: List[Path] = []
        total = len(text_segments) + len([c for c in comment_segments if c])
        completed = 0

        def _report_segment(seg_text: str, seg_voice: str):
            """Отправить детальную информацию о сегменте."""
            if detail_callback:
                preview = seg_text[:120].replace("\n", " ")
                detail_callback(completed + 1, total, preview, seg_voice, "edge")

        for i, text in enumerate(text_segments):
            self._raise_if_canceled()
            # Основной текст
            _report_segment(text, self.config.main_voice)
            try:
                path = await self.synthesize_segment(
                    text, self.config.main_voice, self.config.main_speed,
                    chapter_dir, segment_index=i * 2,
                )
            except SynthesisCancelled:
                raise
            except Exception as e:
                logger.error(
                    "Ошибка синтеза сегмента #%d (гл.текст): %s — генерирую тишину. "
                    "Полный текст: %s",
                    i, e, _FAILURES_LOG,
                )
                silence_path = chapter_dir / f"seg_{i * 2:06d}.mp3"
                await self._generate_silence_mp3(silence_path, duration_sec=0.5)
                path = silence_path
                # Всё равно считаем выполненным для прогресса
            audio_paths.append(path)
            completed += 1
            if progress_callback:
                progress_callback(completed, total)

            # Комментарий (если есть для этого сегмента)
            if i < len(comment_segments) and comment_segments[i]:
                self._raise_if_canceled()
                comment = comment_segments[i]
                _report_segment(comment, self.config.comment_voice)
                try:
                    path = await self.synthesize_segment(
                        comment, self.config.comment_voice,
                        self.config.comment_speed, chapter_dir,
                        segment_index=i * 2 + 1,
                    )
                except SynthesisCancelled:
                    raise
                except Exception as e:
                    logger.error(
                        "Ошибка синтеза комментария #%d: %s — генерирую тишину. "
                        "Полный текст: %s",
                        i, e, _FAILURES_LOG,
                    )
                    silence_path = chapter_dir / f"seg_{i * 2 + 1:06d}.mp3"
                    await self._generate_silence_mp3(silence_path, duration_sec=0.5)
                    path = silence_path
                audio_paths.append(path)
                completed += 1
                if progress_callback:
                    progress_callback(completed, total)

        # Склейка будет выполнена в AudioAssembler
        return chapter_dir

    async def get_available_voices(self, lang: str = "") -> List[Dict[str, Any]]:
        """Получение списка доступных голосов Edge TTS.

        Args:
            lang: Код языка для фильтрации (например, "ru"). Если пусто — все голоса.

        Returns:
            Список словарей с информацией о голосах.
        """
        voices = await edge_tts.list_voices()
        result = [
            {
                "name": v["ShortName"],
                "locale": v["Locale"],
                "gender": v["Gender"],
                "friendly_name": v["FriendlyName"],
            }
            for v in voices
        ]
        if lang:
            result = [v for v in result if v["locale"].startswith(lang)]
        return result

    async def close(self):
        """Освобождение ресурсов."""
        pass
