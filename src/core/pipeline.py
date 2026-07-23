"""
Оркестратор — координация всех модулей для создания аудиокниги.
Поток: парсинг → анализ сегментов → синтез → склейка глав → склейка книги.
"""

from __future__ import annotations

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
from .tts_base import SynthesisCancelled
from .audio_assembler import AudioAssembler
from .checkpoint_manager import CheckpointManager, Checkpoint
from src.utils.scope_display import (
    STAGE_PREPARE,
    STAGE_SYNTH,
    STAGE_CHAPTER_MERGE,
    STAGE_BOOK_MERGE,
    STAGE_DONE,
    format_progress_scope_line,
)

logger = logging.getLogger(__name__)


@dataclass
class AppConfig:
    """Полная конфигурация приложения."""
    book_path: Path = Path("")
    output_dir: Path = Path.home() / "audiobooks"
    work_dir: Path = Path.home() / ".audiobook-generator"

    lang: str = "ru"
    chapter_start: int = 0  # 0 = с начала
    chapter_end: int = 0    # 0 = до конца

    comment_config: CommentConfig = field(default_factory=CommentConfig)
    tts_config: TTSConfig = field(default_factory=TTSConfig)


@dataclass
class _ChapterJob:
    """Подготовленная глава: текст разбит, комментарии готовы."""
    chapter_num: int
    title: str
    sentences: List[str]
    comments: List[Optional[str]]
    chapter_dir: Path
    segment_count: int


class Pipeline:
    """Оркестратор процесса создания аудиокниги."""

    _W_PREPARE = 0.08
    _W_SYNTH = 0.72
    _W_CH_MERGE = 0.15
    _W_BOOK = 0.05

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
        self._pause_event.set()
        self._cancel_event = threading.Event()

    async def run(
        self,
        progress_callback: Optional[Callable] = None,
        cancel_event: Optional[threading.Event] = None,
        detail_callback: Optional[Callable] = None,
    ) -> Path:
        """Парсинг → анализ всех сегментов → синтез → склейка глав → склейка книги.

        При отмене после частичного синтеза склеивает готовые главы в partial-файл.
        """
        if cancel_event is None:
            cancel_event = self._cancel_event

        self._temp_dir = self.config.work_dir / "temp_audio"
        self._temp_dir.mkdir(parents=True, exist_ok=True)
        self._chapter_audio_paths = []
        success = False

        try:
            self._report(progress_callback, "Парсинг FB2-файла…", 0.0, stage=STAGE_PREPARE)
            book = self.fb2_parser.parse(self.config.book_path)
            self._book = book
            if not book.chapters:
                raise ValueError("В книге нет глав")

            total_chapters = len(book.chapters)
            cfg_start = self.config.chapter_start or 0
            cfg_end = self.config.chapter_end or 0
            start_chapter = cfg_start
            end_chapter = cfg_end or total_chapters
            chapters_to_process = book.chapters[start_chapter:end_chapter]
            if not chapters_to_process:
                raise ValueError("В выбранном диапазоне нет глав")

            ui_lang = self.config.lang or "ru"

            def _scope_line(chapter_1based: Optional[int] = None) -> str:
                return format_progress_scope_line(
                    chapter_current=chapter_1based,
                    chapter_start=cfg_start,
                    chapter_end=cfg_end,
                    total_chapters=total_chapters,
                    lang=ui_lang,
                )

            self._report(
                progress_callback,
                f"Книга: «{book.metadata.title}» — анализ сегментов…",
                0.02,
                stage=STAGE_PREPARE,
                scope_line=_scope_line(),
            )

            jobs: List[_ChapterJob] = []
            n_ch = len(chapters_to_process)
            for idx, chapter in enumerate(chapters_to_process):
                if cancel_event.is_set():
                    break
                self._pause_event.wait()

                chapter_num = start_chapter + idx
                scope_now = _scope_line(chapter_num + 1)
                prep_prog = 0.02 + (idx / n_ch) * (self._W_PREPARE - 0.02)
                self._report(
                    progress_callback,
                    "Разбиение на предложения…",
                    prep_prog,
                    stage=STAGE_PREPARE,
                    scope_line=scope_now,
                )

                sentences = self.sentence_splitter.split(
                    " ".join(chapter.paragraphs),
                    book.metadata.lang,
                )
                if not sentences:
                    logger.warning("Глава %d пуста, пропуск", chapter_num + 1)
                    continue

                if self.config.comment_config.enabled:
                    self._report(
                        progress_callback,
                        "Генерация комментариев…",
                        prep_prog,
                        stage=STAGE_PREPARE,
                        scope_line=scope_now,
                    )
                    comments = await self.comment_manager.generate_all(
                        sentences, progress_callback=None,
                    )
                else:
                    comments = [None] * len(sentences)

                seg_count = len(sentences) + sum(1 for c in comments if c)
                chapter_dir = self._temp_dir / f"chapter_{chapter_num:04d}"
                chapter_dir.mkdir(parents=True, exist_ok=True)
                jobs.append(_ChapterJob(
                    chapter_num=chapter_num,
                    title=chapter.title or f"Глава {chapter_num + 1}",
                    sentences=sentences,
                    comments=comments,
                    chapter_dir=chapter_dir,
                    segment_count=seg_count,
                ))

            if not jobs:
                if cancel_event.is_set():
                    raise SynthesisCancelled("Создание аудиокниги отменено")
                raise ValueError("Нет глав для озвучки после разбиения")

            total_segments = sum(j.segment_count for j in jobs)
            n_jobs = len(jobs)
            self._report(
                progress_callback,
                f"Сегментов к синтезу: {total_segments} в {n_jobs} гл.",
                self._W_PREPARE,
                stage=STAGE_PREPARE,
                scope_line=_scope_line(),
                segment_index=0,
                segment_total=total_segments,
            )

            synthesized: List[_ChapterJob] = []
            global_done = 0

            for job_idx, job in enumerate(jobs):
                if cancel_event.is_set():
                    break
                self._pause_event.wait()

                scope_now = _scope_line(job.chapter_num + 1)
                self._report(
                    progress_callback,
                    f"Синтез главы {job_idx + 1}/{n_jobs}…",
                    self._W_PREPARE + self._W_SYNTH * (global_done / max(total_segments, 1)),
                    stage=STAGE_SYNTH,
                    scope_line=scope_now,
                    segment_index=global_done,
                    segment_total=total_segments,
                )

                seg_base = global_done

                def _detail_cb(
                    completed: int,
                    total: int,
                    text: str,
                    voice: str,
                    backend: str,
                    *,
                    _base=seg_base,
                    _scope=scope_now,
                ):
                    # completed — номер текущего (ещё не завершённого) сегмента в главе
                    current = _base + completed
                    prog = self._W_PREPARE + self._W_SYNTH * (
                        (_base + completed - 1) / max(total_segments, 1)
                    )
                    if progress_callback:
                        progress_callback(
                            f"Сегмент {current}/{total_segments}",
                            prog,
                            stage=STAGE_SYNTH,
                            scope_line=_scope,
                            current_text=text,
                            voice=voice,
                            engine=backend,
                            segment_index=current,
                            segment_total=total_segments,
                        )
                    if detail_callback:
                        detail_callback(current, total_segments, text, voice, backend)

                def _seg_progress(
                    completed: int,
                    total: int,
                    *,
                    _base=seg_base,
                    _scope=scope_now,
                ):
                    # completed — число завершённых сегментов в текущей главе
                    finished = _base + completed
                    prog = self._W_PREPARE + self._W_SYNTH * (
                        finished / max(total_segments, 1)
                    )
                    if progress_callback:
                        progress_callback(
                            f"Сегмент {finished}/{total_segments}",
                            prog,
                            stage=STAGE_SYNTH,
                            scope_line=_scope,
                            segment_index=finished,
                            segment_total=total_segments,
                        )

                await self.tts_manager.synthesize_chapter(
                    text_segments=job.sentences,
                    comment_segments=job.comments,
                    chapter_dir=job.chapter_dir,
                    progress_callback=_seg_progress,
                    detail_callback=_detail_cb,
                )
                global_done += job.segment_count
                synthesized.append(job)

                self.checkpoint_manager.save(Checkpoint(
                    book_path=str(self.config.book_path),
                    last_completed_chapter=job.chapter_num,
                    total_chapters=total_chapters,
                    config_hash=CheckpointManager.compute_config_hash({
                        "book_path": str(self.config.book_path),
                        "lang": self.config.lang,
                        "comment_frequency": self.config.comment_config.frequency,
                        "provider": self.config.comment_config.provider,
                    }),
                    timestamp=time.time(),
                    output_dir=str(self._temp_dir),
                ))

            canceled = cancel_event.is_set()
            if not synthesized:
                raise SynthesisCancelled("Создание аудиокниги отменено") if canceled else ValueError(
                    "Нет синтезированных глав"
                )

            self._chapter_audio_paths = []
            n_syn = len(synthesized)
            total_merge_frags = sum(j.segment_count for j in synthesized)
            merge_done = 0

            self._report(
                progress_callback,
                f"Склейка глав: сегментов {total_merge_frags}…",
                self._W_PREPARE + self._W_SYNTH,
                stage=STAGE_CHAPTER_MERGE,
                scope_line=_scope_line(),
                segment_index=0,
                segment_total=total_merge_frags,
            )

            for midx, job in enumerate(synthesized):
                self._pause_event.wait()
                scope_now = _scope_line(job.chapter_num + 1)

                def _frag_progress(
                    completed: int,
                    total: int,
                    *,
                    _base=merge_done,
                    _scope=scope_now,
                    _midx=midx,
                ):
                    finished = _base + completed
                    prog = (
                        self._W_PREPARE + self._W_SYNTH
                        + self._W_CH_MERGE * (finished / max(total_merge_frags, 1))
                    )
                    if progress_callback:
                        progress_callback(
                            f"Склейка главы {_midx + 1}/{n_syn}: "
                            f"сегмент {finished}/{total_merge_frags}",
                            prog,
                            stage=STAGE_CHAPTER_MERGE,
                            scope_line=_scope,
                            segment_index=finished,
                            segment_total=total_merge_frags,
                        )

                chapter_audio = await self._assemble_chapter_audio(
                    job.sentences, job.comments, job.chapter_dir, job.chapter_num,
                    fragment_callback=_frag_progress,
                )
                self._chapter_audio_paths.append(chapter_audio)
                merge_done += job.segment_count

            if not self._chapter_audio_paths:
                raise ValueError("Не удалось склеить ни одной главы")

            partial = canceled or len(synthesized) < n_jobs
            self._report(
                progress_callback,
                "Склеивание книги суммарно…"
                + (" (частичный результат)" if partial else ""),
                0.95,
                stage=STAGE_BOOK_MERGE,
                scope_line=_scope_line(),
                segment_index=len(self._chapter_audio_paths),
                segment_total=n_jobs,
            )

            title = book.metadata.title or "audiobook"
            if partial:
                first = synthesized[0].chapter_num + 1
                last = (
                    synthesized[len(self._chapter_audio_paths) - 1].chapter_num + 1
                )
                title = f"{title} (главы {first}-{last})"

            output_filename = "".join(
                c for c in f"{title}.mp3" if c.isalnum() or c in " .-_()"
            ).strip()
            self.config.output_dir.mkdir(parents=True, exist_ok=True)
            output_path = self.config.output_dir / output_filename
            self.audio_assembler.assemble_book(
                self._chapter_audio_paths,
                output_path,
            )

            success = True
            msg = (
                f"Частичная аудиокнига готова: {output_path}"
                if partial else f"Аудиокнига готова: {output_path}"
            )
            self._report(
                progress_callback, msg, 1.0,
                stage=STAGE_DONE, scope_line=_scope_line(),
            )
            return output_path

        finally:
            if self._temp_dir and self._temp_dir.exists():
                try:
                    self.audio_assembler.cleanup_temp_files(self._temp_dir)
                except Exception as exc:
                    logger.warning("Очистка temp не удалась: %s", exc)
            self.checkpoint_manager.clear()


    async def _assemble_chapter_audio(
        self,
        sentences: List[str],
        comments: List[Optional[str]],
        chapter_dir: Path,
        chapter_num: int,
        fragment_callback: Optional[Callable[[int, int], None]] = None,
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
        return self.audio_assembler.assemble_chapter(
            segments,
            chapter_output,
            fragment_callback=fragment_callback,
        )

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
        callback: Optional[Callable],
        message: str,
        progress: float,
        **details,
    ):
        """Отправка отчёта о прогрессе."""
        if callback:
            try:
                callback(message, progress, **details)
            except TypeError:
                callback(message, progress)
        stage = details.get("stage", "")
        logger.info("[%.0f%%]%s %s", progress * 100, f" [{stage}]" if stage else "", message)

    async def close(self):
        """Освобождение ресурсов."""
        await self.comment_manager.close()
        await self.tts_manager.close()
