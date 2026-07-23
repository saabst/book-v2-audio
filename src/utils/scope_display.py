"""
Форматирование объёма озвучки (полная книга / глава / диапазон)
и коды этапов прогресса.
"""

from __future__ import annotations

from typing import Optional

# Этапы пайплайна (для UI вместо процента)
STAGE_PREPARE = "prepare"
STAGE_SYNTH = "synth"
STAGE_CHAPTER_MERGE = "chapter_merge"
STAGE_BOOK_MERGE = "book_merge"
STAGE_DONE = "done"

STAGE_LABELS = {
    STAGE_PREPARE: "Подготовка…",
    STAGE_SYNTH: "Синтез речи по главам",
    STAGE_CHAPTER_MERGE: "Склеивание по главам",
    STAGE_BOOK_MERGE: "Склеивание книги суммарно",
    STAGE_DONE: "Готово",
}

SCOPE_TEXTS = {
    "ru": {
        "all": "Полный",
        "single": "Глава {}",
        "range": "Главы с {} по {}",
        "total": "всего глав {}",
        "current": "Глава {}",
    },
    "en": {
        "all": "Full book",
        "single": "Chapter {}",
        "range": "Chapters {}–{}",
        "total": "total chapters {}",
        "current": "Chapter {}",
    },
    "ja": {
        "all": "全章",
        "single": "第{}章",
        "range": "第{}章〜第{}章",
        "total": "総章数 {}",
        "current": "第{}章",
    },
    "zh": {
        "all": "全书",
        "single": "第{}章",
        "range": "第{}章至第{}章",
        "total": "总章节 {}",
        "current": "第{}章",
    },
}


def format_scope(
    chapter_start: int,
    chapter_end: int,
    total_chapters: Optional[int] = None,
    lang: str = "ru",
) -> str:
    """Краткое описание режима объёма по chapter_start/chapter_end.

    chapter_start/end — как в Settings/pipeline:
    (0, 0) = вся книга; иначе start 0-based, end exclusive (1-based в UI).
    """
    t = SCOPE_TEXTS.get(lang, SCOPE_TEXTS["ru"])
    start = int(chapter_start or 0)
    end = int(chapter_end or 0)

    if start == 0 and end == 0:
        return t["all"]
    if end == start + 1:
        return t["single"].format(start + 1)

    from_ch = start + 1
    to_ch = end if end > 0 else (total_chapters or from_ch)
    return t["range"].format(from_ch, to_ch)


def format_progress_scope_line(
    *,
    chapter_current: Optional[int],
    chapter_start: int,
    chapter_end: int,
    total_chapters: int,
    lang: str = "ru",
) -> str:
    """Строка для окна прогресса: текущая глава + режим + всего глав."""
    t = SCOPE_TEXTS.get(lang, SCOPE_TEXTS["ru"])
    scope = format_scope(chapter_start, chapter_end, total_chapters, lang)
    parts = []
    # При режиме «одна глава» scope уже содержит «Глава N» — не дублируем
    single = int(chapter_end or 0) == int(chapter_start or 0) + 1 and int(chapter_end or 0) > 0
    if chapter_current is not None and chapter_current > 0 and not single:
        parts.append(t["current"].format(chapter_current))
    parts.append(scope)
    parts.append(t["total"].format(total_chapters))
    return " · ".join(parts)
