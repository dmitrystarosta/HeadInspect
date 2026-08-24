from __future__ import annotations

from ..htmlmeta import MetadataParser
from ..models import MetaData


TITLE_MIN = 30
TITLE_MAX = 60
DESCRIPTION_MIN = 120
DESCRIPTION_MAX = 160


def _length_warning(label: str, value: str, minimum: int, maximum: int) -> str | None:
    length = len(value)
    if length < minimum:
        return f"{label}: {length} символов — короткое значение, ориентир {minimum}–{maximum}"
    if length > maximum:
        return f"{label}: {length} символов — длинное значение, ориентир {minimum}–{maximum}"
    return None


def analyze_meta(parser: MetadataParser) -> MetaData:
    errors: list[str] = []
    warnings: list[str] = []

    title_values = parser.title_values
    title = parser.title
    if not title_values:
        errors.append("Нет <title>")
    elif not title:
        errors.append("Пустой <title>")
    else:
        warning = _length_warning("<title>", title, TITLE_MIN, TITLE_MAX)
        if warning:
            warnings.append(warning)
    if len(title_values) > 1:
        errors.append(f"Найдено несколько <title>: {len(title_values)}")

    descriptions = parser.meta_values("description")
    description = parser.meta_description
    if not descriptions:
        errors.append("Нет meta description")
    elif not description:
        errors.append("Пустой meta description")
    else:
        warning = _length_warning(
            "meta description", description, DESCRIPTION_MIN, DESCRIPTION_MAX
        )
        if warning:
            warnings.append(warning)
    if len(descriptions) > 1:
        errors.append(f"Найдено несколько meta description: {len(descriptions)}")

    keywords_values = parser.meta_values("keywords")
    keywords = parser.first_meta("keywords")
    if not keywords_values:
        warnings.append(
            "Нет meta keywords — Яндекс указывает, что тег может влиять на соответствие "
            "страницы запросам; Google не использует meta keywords для ранжирования"
        )
    elif not keywords:
        warnings.append("Пустой meta keywords")
    if len(keywords_values) > 1:
        warnings.append(f"Найдено несколько meta keywords: {len(keywords_values)}")

    robots_values = parser.meta_values("robots")
    robots = parser.first_meta("robots")
    if len(robots_values) > 1:
        warnings.append(f"Найдено несколько meta robots: {len(robots_values)}")
    if robots:
        directives = {
            part.strip().lower()
            for part in robots.replace(";", ",").split(",")
            if part.strip()
        }
        if "none" in directives:
            warnings.append("meta robots содержит none (noindex, nofollow)")
        else:
            if "noindex" in directives:
                warnings.append("meta robots содержит noindex")
            if "nofollow" in directives:
                warnings.append("meta robots содержит nofollow")

    viewport_values = parser.meta_values("viewport")
    viewport = parser.first_meta("viewport")
    if not viewport_values:
        warnings.append("Нет meta viewport")
    elif not viewport:
        warnings.append("Пустой meta viewport")
    elif "width=device-width" not in viewport.lower().replace(" ", ""):
        warnings.append("В meta viewport нет width=device-width")
    if len(viewport_values) > 1:
        warnings.append(f"Найдено несколько meta viewport: {len(viewport_values)}")

    if not parser.html_lang:
        warnings.append("Не указан атрибут lang у <html>")
    if not parser.charset:
        warnings.append("Не указана кодировка страницы в meta charset/Content-Type")

    return MetaData(
        title=title,
        title_count=len(title_values),
        description=description,
        description_count=len(descriptions),
        keywords=keywords,
        keywords_count=len(keywords_values),
        robots=robots,
        robots_count=len(robots_values),
        viewport=viewport,
        viewport_count=len(viewport_values),
        lang=parser.html_lang,
        charset=parser.charset,
        errors=errors,
        warnings=warnings,
    )
