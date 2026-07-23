"""
Оркестратор — координация всех модулей для создания аудиокниги.
Управляет потоком: парсинг → разбиение → комментарии → TTS → склейка.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, List, Optional, Tuple

from .fb2_parser import FB2Parser, ParsedBook
from .sentence_splitter import SentenceSplitter
from .comment_manager import CommentManager, CommentConfig
from .tts_manager import TTSManager, TTSConfig
from .audio_assembler import AudioAssembler
from .checkpoint_manager import CheckpointManager, Checkpoint

logger = logging.getLogger(__name__)


@dataclass
class AppConfig:
    """Полная конфигурация приложения."""
    # Пути
    book_path: Path = Path("")
    output_dir: Path = Path.home() / "audiobooks"
    work_dir: Path = Path.home() / ".audiobook-generator"

    # Настройки книги
    lang: str = "ru"
    chapter_start: int = 0  # 0 = с начала
    chapter_end: int = 0    # 0 = до конца

    # Комментарии
    comment_config: CommentConfig = field(default_factory=CommentConfig)

    # TTS
    tts_config: TTSConfig = field(default_factory=TTSConfig)


class Pipeline:
    """Оркестратор процесса создания аудиокниги.

    Пример использования:
        config = AppConfig(
            book_path=Path("book.fb2"),
            output_dir=Path("./output"),
        )
        pipeline = Pipeline(config)
        result = await pipeline.run(
            progress_callback=lambda c, t: print(f"{c}/{t}"),
            cancel_event=threading.Event(),
        )
    """

    def __init__(self, config: AppConfig):
        self.config = config
        self.fb2_parser = FB2Parser()
        self.sentence_splitter = SentenceSplitter()
        self.comment_manager = CommentManager(config.comment_config)
        self.tts_manager = TTSManager(config.tts_config)
        self.audio_assembler = AudioAssembler()
        self.checkpoint_manager = CheckpointManager(config.work_dir)

        self._book: Optional[ParsedBook] = None
        self._temp_dir: Optional[Path] = None
        self._chapter_audio_paths: List[Path] = []
        self._paused = False
        self._pause_event = threading.Event()
        self._pause_event.set()  # не на паузе
        self._cancel_event = threading.Event()  # не отменён

    async def run(
        self,
        progress_callback: Optional[Callable[[str, float], None]] = None,
        cancel_event: Optional[threading.Event] = None,
        detail_callback: Optional[Callable[[int, int, str, str, str], None]] = None,
    ) -> Path:
        """Запуск полного процесса создания аудиокниги.

        Args:
            progress_callback: Колбэк прогресса (статус, процент 0.0-1.0).
            cancel_event: Событие отмены.
            detail_callback: Колбэк деталей синтеза (номер, всего, текст, голос, движок).

        Returns:
            Путь к финальному MP3-файлу.

        Raises:
            ValueError: Если книга не загружена.
        """
        if cancel_event is None:
            cancel_event = self._cancel_event

        self._temp_dir = self.config.work_dir / "temp_audio"
        self._chapter_audio_paths = []

        try:
            # Шаг 1: Парсинг FB2
            self._report(progress_callback, "Парсинг FB2-файла...", 0.0)
            book = self.fb2_parser.parse(self.config.book_path)
            self._book = book

            if not book.chapters:
                raise ValueError("В книге нет глав")

            total_chapters = len(book.chapters)
            start_chapter = self.config.chapter_start or 0
            end_chapter = self.config.chapter_end or total_chapters
            chapters_to_process = book.chapters[start_chapter:end_chapter]

            self._report(
                progress_callback,
                f"Книга: '{book.metadata.title}', глав: {len(chapters_to_process)}",
                0.05,
            )

            # Проверка чекпоинта
            checkpoint = self.checkpoint_manager.load()
            resume_from = 0
            if checkpoint and checkpoint.book_path == str(self.config.book_path):
                resume_from = checkpoint.last_completed_chapter + 1

                # Если чекпоинт указывает на главу вне текущего диапазона — очищаем и начинаем сначала
                if resume_from >= end_chapter:
                    logger.warning(
                        "Чекпоинт (глава %d) вне диапазона [%d, %d), очищаю и начинаю сначала",
                        checkpoint.last_completed_chapter, start_chapter, end_chapter,
                    )
                    self.checkpoint_manager.clear()
                    resume_from = 0
                else:
                    self._report(
                        progress_callback,
                        f"Восстановление с главы {resume_from + 1}...",
                        0.05,
                    )

            # Шаг 2-4: Обработка каждой главы
            for idx, chapter in enumerate(chapters_to_process):
                if cancel_event.is_set():
                    self._report(progress_callback, "Процесс отменён", 0.0)
                    break

                # Ожидание снятия паузы
                self._pause_event.wait()

                chapter_num = start_chapter + idx
                if chapter_num < resume_from:
                    continue

                chapter_progress = 0.1 + (idx / len(chapters_to_process)) * 0.8
                chapter_label = f"Глава {chapter_num + 1}/{total_chapters}"
                if chapter.title:
                    title = chapter.title.strip()
                    if len(title) > 72:
                        title = title[:69] + "…"
                    chapter_label = f"{chapter_label} «{title}»"

                # Разбиение на предложения
                self._report(
                    progress_callback,
                    f"{chapter_label}: разбиение на предложения...",
                    chapter_progress,
                )
                sentences = self.sentence_splitter.split(
                    " ".join(chapter.paragraphs),
                    book.metadata.lang,
                )

                if not sentences:
                    logger.warning("Глава %d пуста, пропуск", chapter_num + 1)
                    continue

                # Генерация комментариев (если включены)
                if self.config.comment_config.enabled:
                    self._report(
                        progress_callback,
                        f"{chapter_label}: генерация комментариев...",
                        chapter_progress + 0.05,
                    )
                    comments = await self.comment_manager.generate_all(
                        sentences,
                        progress_callback=None,
                    )
                else:
                    comments = [None] * len(sentences)

                # Синтез речи
                self._report(
                    progress_callback,
                    f"{chapter_label}: синтез речи...",
                    chapter_progress + 0.2,
                )

                chapter_dir = self._temp_dir / f"chapter_{chapter_num:04d}"
                await self.tts_manager.synthesize_chapter(
                    text_segments=sentences,
                    comment_segments=comments,
                    chapter_dir=chapter_dir,
                    detail_callback=detail_callback,
                )

                # Склейка аудиофрагментов главы
                self._report(
                    progress_callback,
                    f"{chapter_label}: склейка аудио...",
                    chapter_progress + 0.4,
                )

                chapter_audio = await self._assemble_chapter_audio(
                    sentences, comments, chapter_dir, chapter_num,
                )
                self._chapter_audio_paths.append(chapter_audio)

                # Сохранение чекпоинта
                config_dict = {
                    "book_path": str(self.config.book_path),
                    "lang": self.config.lang,
                    "comment_frequency": self.config.comment_config.frequency,
                    "provider": self.config.comment_config.provider,
                }
                self.checkpoint_manager.save(Checkpoint(
                    book_path=str(self.config.book_path),
                    last_completed_chapter=chapter_num,
                    total_chapters=total_chapters,
                    config_hash=CheckpointManager.compute_config_hash(config_dict),
                    timestamp=time.time(),
                    output_dir=str(self._temp_dir),
                ))

            # Шаг 5: Склейка книги
            if self._chapter_audio_paths:
                if cancel_event.is_set():
                    # Пользователь отменил процесс, но часть глав уже готова
                    self._report(
                        progress_callback,
                        "Процесс отменён. Готовые главы не склеены в книгу.",
                        0.0,
                    )
                    return Path("")
                else:
                    self._report(
                        progress_callback,
                        "Склейка всех глав в аудиокнигу...",
                        0.95,
                    )

                    output_filename = f"{book.metadata.title}.mp3"
                    # Очищаем имя файла от недопустимых символов
                    output_filename = "".join(
                        c for c in output_filename
                        if c.isalnum() or c in " .-_()"
                    ).strip()

                    output_path = self.config.output_dir / output_filename
                    self.audio_assembler.assemble_book(
                        self._chapter_audio_paths,
                        output_path,
                    )

                    # Очистка чекпоинта
                    self.checkpoint_manager.clear()

                    self._report(
                        progress_callback,
                        f"Аудиокнига готова: {output_path}",
                        1.0,
                    )

                    return output_path

            else:
                if cancel_event.is_set():
                    raise ValueError("Создание аудиокниги отменено")
                else:
                    raise ValueError("Не удалось создать аудиокнигу: нет обработанных глав")

        finally:
            # Очистка временных файлов
            if self._temp_dir and self._temp_dir.exists():
                self.audio_assembler.cleanup_temp_files(self._temp_dir)

            # Очищаем чекпоинт при любом прерывании (кроме успешного завершения):
            # временные файлы удалены, так что чекпоинт бесполезен.
            # Если дошли до return output_path в строке 245 — clear() уже вызван,
            # вызывать повторно безвредно (clear проверяет exists()).
            self.checkpoint_manager.clear()

    async def _assemble_chapter_audio(
        self,
        sentences: List[str],
        comments: List[Optional[str]],
        chapter_dir: Path,
        chapter_num: int,
    ) -> Path:
        """Сборка аудиофрагментов главы в один файл."""
        segments: List[Tuple[Path, float]] = []
        tts_cfg = self.config.tts_config

        # Собираем все mp3 файлы из директории главы
        # PiperTTSManager использует seg_*.mp3 (индексные имена),
        # EdgeTTSManager использует segment_*.mp3 (хеш-имена)
        audio_files = sorted(chapter_dir.glob("*.mp3"))

        # Формируем сегменты с паузами
        audio_idx = 0
        for i in range(len(sentences)):
            if audio_idx < len(audio_files):
                # Основной текст
                segments.append((audio_files[audio_idx], tts_cfg.pause_between_sentences))
                audio_idx += 1

            # Комментарий
            if i < len(comments) and comments[i] and audio_idx < len(audio_files):
                segments.append((audio_files[audio_idx], tts_cfg.pause_before_comment))
                audio_idx += 1
                # Добавляем паузу после комментария к последнему сегменту
                if segments:
                    segments[-1] = (segments[-1][0], tts_cfg.pause_after_comment)

        chapter_output = self._temp_dir / f"chapter_{chapter_num:04d}_audio.wav"
        return self.audio_assembler.assemble_chapter(segments, chapter_output)

    def pause(self):
        """Поставить процесс на паузу."""
        self._paused = True
        self._pause_event.clear()
        logger.info("Процесс поставлен на паузу")

    def resume(self):
        """Снять процесс с паузы."""
        self._paused = False
        self._pause_event.set()
        logger.info("Процесс возобновлён")

    def cancel(self):
        """Отменить процесс."""
        self._cancel_event.set()
        self._pause_event.set()  # снимаем с паузы, чтобы процесс завершился
        logger.info("Процесс отменён")

    def is_canceled(self) -> bool:
        """Проверка, был ли процесс отменён."""
        return self._cancel_event.is_set()

    def is_paused(self) -> bool:
        """Проверка, стоит ли процесс на паузе."""
        return self._paused

    def _report(
        self,
        callback: Optional[Callable[[str, float], None]],
        message: str,
        progress: float,
    ):
        """Отправка отчёта о прогрессе."""
        if callback:
            callback(message, progress)
        logger.info("[%.0f%%] %s", progress * 100, message)

    async def close(self):
        """Освобождение ресурсов."""
        await self.comment_manager.close()
        await self.tts_manager.close()
