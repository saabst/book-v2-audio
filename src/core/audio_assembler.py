"""
Модуль склейки аудиофайлов.
Объединяет аудиофрагменты в главы и главы в финальный MP3-файл.
Использует прямой вызов ffmpeg вместо pydub (pydub требует audioop,
который удалён из стандартной библиотеки Python 3.13+).
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess as sp
import tempfile
from pathlib import Path
from typing import Callable, List, Optional, Tuple

from src.core.audio_bitrate import ffmpeg_lame_bitrate_args

logger = logging.getLogger(__name__)


class AudioAssembler:
    """Склейка аудиофрагментов в главы и книгу через ffmpeg.

    Пример использования:
        assembler = AudioAssembler()
        # Склейка главы
        chapter_path = assembler.assemble_chapter(
            segments=[("path1.wav", 0.3), ("path2.wav", 1.0)],
            output_path=Path("chapter1.wav"),
        )
        # Склейка книги
        book_path = assembler.assemble_book(
            chapter_paths=[Path("chapter1.wav"), Path("chapter2.wav")],
            output_path=Path("book.mp3"),
        )
    """

    def __init__(self, sample_rate: int = 22050, bitrate_kbps: int = 128):
        """Инициализация AudioAssembler.

        Args:
            sample_rate: Частота дискретизации для выходных WAV-файлов.
                По умолчанию 22050 Гц — стандартная частота моделей Piper TTS.
                Edge TTS (через ffmpeg) будет передискретизирован под эту частоту
                при склейке, что обеспечивает консистентность всех аудиофрагментов
                и предотвращает белый шум из-за mismatch в ffmpeg concat demuxer.
            bitrate_kbps: Битрейт финального MP3 (CBR).
        """
        self.sample_rate = sample_rate
        self.bitrate_kbps = int(bitrate_kbps) or 128
        self._ffmpeg = self._find_ffmpeg()

    # ── helpers ────────────────────────────────────────────────

    @staticmethod
    def _find_ffmpeg() -> str:
        """Поиск ffmpeg в PATH."""
        exe = shutil.which("ffmpeg")
        if exe is None:
            raise RuntimeError(
                "ffmpeg не найден. Установите ffmpeg:\n"
                "  sudo apt install ffmpeg          # Debian/Ubuntu\n"
                "  sudo dnf install ffmpeg          # Fedora\n"
                "  brew install ffmpeg              # macOS\n"
                "  winget install ffmpeg            # Windows"
            )
        return exe

    def _run_ffmpeg(self, args: List[str], **kwargs) -> None:
        """Запуск ffmpeg с логированием."""
        cmd = [self._ffmpeg] + args
        logger.debug("ffmpeg: %s", " ".join(str(a) for a in cmd))
        try:
            sp.run(cmd, check=True, capture_output=True, **kwargs)
        except sp.CalledProcessError as exc:
            stderr = exc.stderr.decode("utf-8", errors="replace") if exc.stderr else ""
            logger.error("ffmpeg error (code %d): %s", exc.returncode, stderr)
            raise RuntimeError(
                f"Ошибка ffmpeg (код {exc.returncode}): {stderr[:500]}"
            ) from exc

    def _make_silence(self, path: Path, duration_sec: float) -> Path:
        """Создание WAV-файла с тишиной заданной длительности."""
        duration_ms = int(duration_sec * 1000)
        self._run_ffmpeg([
            "-f", "lavfi",
            "-i", f"anullsrc=r={self.sample_rate}:cl=mono",
            "-t", str(duration_sec),
            "-acodec", "pcm_s16le",
            str(path),
        ])
        return path

    # ── public API ─────────────────────────────────────────────

    def assemble_chapter(
        self,
        segments: List[Tuple[Path, float]],
        output_path: Path,
    ) -> Path:
        """Склейка аудиофрагментов в главу.

        Все входные файлы пре-декодируются в WAV (PCM s16le, 1ch, sample_rate Гц),
        чтобы ffmpeg concat demuxer не путал кодеки (WAV-тишина + MP3-сегменты)
        и не выдавал белый шум при попытке декодировать MP3-фреймы как PCM-данные.

        Args:
            segments: Список кортежей (путь_к_аудио, пауза_перед_в_сек).
            output_path: Путь для сохранения готовой главы.

        Returns:
            Путь к готовому аудиофайлу главы.
        """
        logger.info("Склейка главы: %d фрагментов", len(segments))
        output_path.parent.mkdir(parents=True, exist_ok=True)

        with tempfile.TemporaryDirectory(prefix="chapter_") as tmp_dir:
            tmp_root = Path(tmp_dir)
            # Все файлы пре-декодируем в WAV с едиными параметрами,
            # чтобы concat demuxer не получил на вход смесь разных кодеков
            wav_files: List[Path] = []

            for idx, (audio_path, pause) in enumerate(segments):
                if pause > 0:
                    silence_path = tmp_root / f"silence_{idx}.wav"
                    self._make_silence(silence_path, pause)
                    wav_files.append(silence_path)

                if not audio_path.exists():
                    logger.warning("Файл не найден: %s", audio_path)
                    continue

                # Пре-декодируем каждый сегмент в WAV с едиными параметрами
                wav_segment = tmp_root / f"seg_{idx}.wav"
                self._run_ffmpeg([
                    "-y", "-i", str(audio_path),
                    "-acodec", "pcm_s16le",
                    "-ac", "1",
                    "-ar", str(self.sample_rate),
                    str(wav_segment),
                ])
                wav_files.append(wav_segment)

            if not wav_files:
                raise FileNotFoundError("Нет ни одного валидного аудиофайла для склейки главы")

            # Создаём файл-список для ffmpeg concat demuxer
            list_file = tmp_root / "filelist.txt"
            with open(list_file, "w", encoding="utf-8") as f:
                for p in wav_files:
                    f.write(f"file '{p.resolve()}'\n")

            self._run_ffmpeg([
                "-f", "concat",
                "-safe", "0",
                "-i", str(list_file),
                "-acodec", "pcm_s16le",
                "-ac", "1",
                "-ar", str(self.sample_rate),
                "-y", str(output_path),
            ])

        # Получаем длительность через ffprobe
        duration = self.get_audio_duration(output_path)
        logger.info("Глава сохранена: %s (%.1f сек)", output_path, duration)
        return output_path

    def assemble_book(
        self,
        chapter_paths: List[Path],
        output_path: Path,
        chapter_pause: float = 1.5,
        progress_callback: Optional[Callable[[int, int], None]] = None,
    ) -> Path:
        """Склейка глав в финальную аудиокнигу.

        Args:
            chapter_paths: Список путей к аудиофайлам глав.
            output_path: Путь для сохранения финального MP3.
            chapter_pause: Пауза между главами в секундах.
            progress_callback: Колбэк прогресса (текущая глава, всего).

        Returns:
            Путь к финальному MP3-файлу.
        """
        logger.info(
            "Склейка книги: %d глав -> %s",
            len(chapter_paths), output_path,
        )
        output_path.parent.mkdir(parents=True, exist_ok=True)

        with tempfile.TemporaryDirectory(prefix="book_") as tmp_dir:
            tmp_root = Path(tmp_dir)
            file_list_paths: List[Path] = []
            total = len(chapter_paths)

            for i, chapter_path in enumerate(chapter_paths):
                if i > 0 and chapter_pause > 0:
                    silence_path = tmp_root / f"pause_{i}.wav"
                    self._make_silence(silence_path, chapter_pause)
                    file_list_paths.append(silence_path)

                if not chapter_path.exists():
                    logger.warning("Глава не найдена: %s", chapter_path)
                    continue

                file_list_paths.append(chapter_path)

                if progress_callback:
                    progress_callback(i + 1, total)

            if not file_list_paths:
                raise FileNotFoundError("Нет ни одной валидной главы для склейки книги")

            # Создаём файл-список для ffmpeg concat demuxer
            list_file = tmp_root / "filelist.txt"
            with open(list_file, "w", encoding="utf-8") as f:
                for p in file_list_paths:
                    f.write(f"file '{p.resolve()}'\n")

            # Конвертируем всё в WAV (чтобы concat работал с одинаковым кодеком)
            merged_wav = tmp_root / "merged.wav"
            self._run_ffmpeg([
                "-f", "concat",
                "-safe", "0",
                "-i", str(list_file),
                "-acodec", "pcm_s16le",
                "-ac", "1",
                "-ar", str(self.sample_rate),
                "-y", str(merged_wav),
            ])

            # Финальное кодирование в MP3 (явный CBR без -q:a)
            self._run_ffmpeg([
                "-i", str(merged_wav),
                *ffmpeg_lame_bitrate_args(self.bitrate_kbps),
                "-y", str(output_path),
            ])

        duration_min = self.get_audio_duration(output_path) / 60
        logger.info(
            "Аудиокнига сохранена: %s (%.1f мин)",
            output_path, duration_min,
        )

        return output_path

    def cleanup_temp_files(self, temp_dir: Path):
        """Удаление временных файлов.

        Args:
            temp_dir: Директория с временными файлами.
        """
        if temp_dir.exists():
            shutil.rmtree(temp_dir)
            logger.debug("Временные файлы удалены: %s", temp_dir)

    def get_audio_duration(self, audio_path: Path) -> float:
        """Получение длительности аудиофайла в секундах через ffprobe.

        Args:
            audio_path: Путь к аудиофайлу.

        Returns:
            Длительность в секундах.
        """
        if not audio_path.exists():
            return 0.0

        ffprobe = shutil.which("ffprobe")
        if ffprobe is None:
            logger.warning("ffprobe не найден, возвращаю 0")
            return 0.0

        try:
            result = sp.run(
                [
                    ffprobe,
                    "-v", "quiet",
                    "-print_format", "json",
                    "-show_format",
                    str(audio_path),
                ],
                capture_output=True,
                check=True,
            )
            data = json.loads(result.stdout)
            return float(data["format"]["duration"])
        except Exception as exc:
            logger.warning("Не удалось получить длительность %s: %s", audio_path, exc)
            return 0.0
