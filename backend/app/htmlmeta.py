from __future__ import annotations

from html.parser import HTMLParser


class MetadataParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.in_title = False
        self.title_parts: list[str] = []
        self.meta_description: str | None = None
        self.og: dict[str, list[str]] = {}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()

        if tag == "title":
            self.in_title = True
            return

        if tag != "meta":
            return

        data = {k.lower(): (v or "") for k, v in attrs}
        content = data.get("content", "").strip()

        name = data.get("name", "").strip().lower()
        if name == "description" and content and self.meta_description is None:
            self.meta_description = content

        prop = data.get("property", "").strip().lower()
        if prop.startswith("og:") and content:
            self.og.setdefault(prop, []).append(content)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "title":
            self.in_title = False

    def handle_data(self, data: str) -> None:
        if self.in_title:
            self.title_parts.append(data)

    @property
    def title(self) -> str | None:
        value = " ".join(part.strip() for part in self.title_parts if part.strip()).strip()
        return value or None
