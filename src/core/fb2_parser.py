"""
Модуль парсинга FB2-файлов.
Извлекает структуру глав, метаданные и очищает текст от нетекстовых элементов.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

import defusedxml.ElementTree as ET

logger = logging.getLogger(__name__)


@dataclass
class BookMetadata:
    """Метаданные книги."""
    title: str = ""
    author: str = ""
    lang: str = "ru"  # ru, en, ja, zh


@dataclass
class Chapter:
    """Глава книги."""
    title: str = ""
    paragraphs: List[str] = field(default_factory=list)


@dataclass
class ParsedBook:
    """Результат парсинга книги."""
    metadata: BookMetadata = field(default_factory=BookMetadata)
    chapters: List[Chapter] = field(default_factory=list)


class FB2Parser:
    """Парсер FB2-файлов.

    Пример использования:
        parser = FB2Parser()
        book = parser.parse("book.fb2")
        print(book.metadata.title)  # Название книги
        print(len(book.chapters))   # Количество глав
    """

    # Пространства имён FB2
    NS = {
        "fb": "http://www.gribuser.ru/xml/fictionbook/2.0",
        "xlink": "http://www.w3.org/1999/xlink",
    }

    # Разделитель пути для вложенных секций: «ЧАСТЬ › ЯПОНИЯ»
    TITLE_PATH_SEP = " › "

    # Теги, которые нужно удалить из текста
    REMOVE_TAGS = {
        "image", "empty-line", "poem", "subtitle",
        "epigraph", "cite", "stanza", "v",
    }

    def parse(self, path: Path | str) -> ParsedBook:
        """Парсинг FB2-файла.

        Args:
            path: Путь к FB2-файлу.

        Returns:
            ParsedBook с метаданными и главами.

        Raises:
            FileNotFoundError: Если файл не найден.
            ValueError: Если файл не является валидным FB2.
        """
        path = Path(path)
        if not path.exists():
            raise FileNotFoundError(f"Файл не найден: {path}")

        logger.info("Парсинг FB2: %s", path)

        try:
            tree = ET.parse(str(path))
            root = tree.getroot()
        except ET.ParseError as e:
            raise ValueError(f"Ошибка парсинга XML: {e}")

        # Определяем, используется ли пространство имён
        has_ns = "{" in root.tag
        ns = self.NS if has_ns else {}

        metadata = self._parse_metadata(root, ns)
        chapters = self._parse_chapters(root, ns)

        logger.info(
            "Завершён парсинг: '%s' (%s), глав: %d",
            metadata.title, metadata.lang, len(chapters),
        )

        return ParsedBook(metadata=metadata, chapters=chapters)

    def _parse_metadata(self, root: ET.Element, ns: dict) -> BookMetadata:
        """Извлечение метаданных из FB2."""
        metadata = BookMetadata()

        title_info = root.find(".//fb:title-info", ns) if ns else root.find(".//title-info")
        if title_info is None:
            logger.warning("Не найден блок title-info")
            return metadata

        # Название
        title_elem = title_info.find("fb:book-title", ns) if ns else title_info.find("book-title")
        if title_elem is not None and title_elem.text:
            metadata.title = title_elem.text.strip()

        # Автор
        author = title_info.find("fb:author", ns) if ns else title_info.find("author")
        if author is not None:
            first_name = self._get_text(author, "fb:first-name", ns) if ns else self._get_text(author, "first-name", {})
            last_name = self._get_text(author, "fb:last-name", ns) if ns else self._get_text(author, "last-name", {})
            metadata.author = f"{first_name} {last_name}".strip()

        # Язык
        lang_elem = title_info.find("fb:lang", ns) if ns else title_info.find("lang")
        if lang_elem is not None and lang_elem.text:
            metadata.lang = lang_elem.text.strip().lower()[:2]

        return metadata

    @staticmethod
    def _local_tag(element: ET.Element) -> str:
        tag = element.tag
        return tag.split("}")[-1] if "}" in tag else tag

    def _parse_chapters(self, root: ET.Element, ns: dict) -> List[Chapter]:
        """Извлечение глав из FB2.

        Вложенные ``<section>`` разворачиваются в отдельные главы.
        Заголовок — путь: «ЧАСТЬ ПЕРВАЯ › ЯПОНИЯ».
        """
        chapters: List[Chapter] = []

        body = root.find("fb:body", ns) if ns else root.find("body")
        if body is None:
            logger.warning("Не найден блок body")
            return chapters

        sections = body.findall("fb:section", ns) if ns else body.findall("section")

        if not sections:
            chapter = self._chapter_from_own_content(body, ns, path=[])
            if chapter is not None:
                chapters.append(chapter)
        else:
            for section in sections:
                self._walk_section(section, ns, path=[], out=chapters)

        return chapters

    def _walk_section(
        self,
        section: ET.Element,
        ns: dict,
        path: List[str],
        out: List[Chapter],
    ) -> None:
        """Рекурсивный обход секций: leaf и intro-текст родителя → главы."""
        title = self._section_title(section, ns)
        new_path = path + ([title] if title else [])
        children = (
            section.findall("fb:section", ns) if ns else section.findall("section")
        )
        own = self._own_paragraphs(section, ns)

        if children:
            # Текст дочерних секций не включаем; собственный intro — отдельная глава
            if own:
                out.append(Chapter(
                    title=self.TITLE_PATH_SEP.join(new_path) if new_path else "",
                    paragraphs=own,
                ))
            for child in children:
                self._walk_section(child, ns, new_path, out)
            return

        if own:
            out.append(Chapter(
                title=self.TITLE_PATH_SEP.join(new_path) if new_path else "",
                paragraphs=own,
            ))

    def _chapter_from_own_content(
        self,
        section: ET.Element,
        ns: dict,
        path: List[str],
    ) -> Optional[Chapter]:
        own = self._own_paragraphs(section, ns)
        if not own:
            return None
        return Chapter(
            title=self.TITLE_PATH_SEP.join(path) if path else self._section_title(section, ns),
            paragraphs=own,
        )

    def _section_title(self, section: ET.Element, ns: dict) -> str:
        """Заголовок секции; несколько <p> в <title> склеиваются через пробел."""
        title_elem = section.find("fb:title", ns) if ns else section.find("title")
        if title_elem is None:
            return ""
        paras = title_elem.findall("fb:p", ns) if ns else title_elem.findall("p")
        if paras:
            parts = [self._get_inner_text(p).strip() for p in paras]
            return " ".join(p for p in parts if p)
        return self._get_inner_text(title_elem).strip()

    def _own_paragraphs(self, section: ET.Element, ns: dict) -> List[str]:
        """Абзацы этой секции без вложенных <section> и без <title>."""
        texts: List[str] = []
        for child in section:
            tag = self._local_tag(child)
            if tag in ("section", "title"):
                continue
            if tag in self.REMOVE_TAGS:
                continue
            if tag == "p":
                text = self._get_inner_text(child).strip()
                if text:
                    texts.append(text)
                continue
            # Прочие контейнеры (annotation и т.п.) — только их <p>
            for p in (child.findall(".//fb:p", ns) if ns else child.findall(".//p")):
                text = self._get_inner_text(p).strip()
                if text:
                    texts.append(text)
        return texts

    def _get_text(self, element: ET.Element, tag: str, ns: dict) -> str:
        """Получение текста из дочернего элемента."""
        child = element.find(tag, ns) if ns else element.find(tag)
        if child is not None and child.text:
            return child.text.strip()
        return ""

    def _get_inner_text(self, element: ET.Element) -> str:
        """Получение всего текста внутри элемента, включая вложенные."""
        parts = []
        self._collect_text(element, parts)
        return "".join(parts)

    def _collect_text(self, element: ET.Element, parts: list) -> None:
        """Рекурсивный сбор текста."""
        if element.text:
            parts.append(element.text)
        for child in element:
            tag = child.tag.split("}")[-1] if "}" in child.tag else child.tag
            if tag not in self.REMOVE_TAGS:
                self._collect_text(child, parts)
            if child.tail:
                parts.append(child.tail)
