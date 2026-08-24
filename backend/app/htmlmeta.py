from __future__ import annotations

import re
from html.parser import HTMLParser


_CHARSET_RE = re.compile(r"charset\s*=\s*([^;\s]+)", re.IGNORECASE)


class MetadataParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.in_title = False
        self.current_title_parts: list[str] = []
        self.title_values: list[str] = []

        self.meta_by_name: dict[str, list[str]] = {}
        self.og: dict[str, list[str]] = {}

        self.html_lang: str | None = None
        self.charset: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        data = {k.lower(): (v or "") for k, v in attrs}

        if tag == "html" and self.html_lang is None:
            self.html_lang = data.get("lang", "").strip() or None
            return

        if tag == "title":
            self.in_title = True
            self.current_title_parts = []
            return

        if tag != "meta":
            return

        if self.charset is None:
            direct_charset = data.get("charset", "").strip()
            if direct_charset:
                self.charset = direct_charset
            else:
                http_equiv = data.get("http-equiv", "").strip().lower()
                if http_equiv == "content-type":
                    match = _CHARSET_RE.search(data.get("content", ""))
                    if match:
                        self.charset = match.group(1).strip("\"'")

        content = data.get("content", "").strip()

        name = data.get("name", "").strip().lower()
        if name:
            # Keep even empty content so the analyzer can distinguish
            # a missing tag from a present-but-empty one.
            self.meta_by_name.setdefault(name, []).append(content)

        prop = data.get("property", "").strip().lower()
        if prop.startswith("og:") and content:
            self.og.setdefault(prop, []).append(content)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() != "title":
            return

        if self.in_title:
            value = " ".join(
                part.strip() for part in self.current_title_parts if part.strip()
            ).strip()
            self.title_values.append(value)

        self.in_title = False
        self.current_title_parts = []

    def handle_data(self, data: str) -> None:
        if self.in_title:
            self.current_title_parts.append(data)

    @property
    def title(self) -> str | None:
        for value in self.title_values:
            if value:
                return value
        return None

    @property
    def meta_description(self) -> str | None:
        return self.first_meta("description")

    def first_meta(self, name: str) -> str | None:
        values = self.meta_by_name.get(name.lower(), [])
        for value in values:
            if value:
                return value
        return None

    def meta_values(self, name: str) -> list[str]:
        return list(self.meta_by_name.get(name.lower(), []))
