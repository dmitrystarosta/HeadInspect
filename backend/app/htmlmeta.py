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

        self.in_json_ld = False
        self.current_json_ld_parts: list[str] = []
        self.json_ld_blocks: list[str] = []
        self.microdata_types: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        data = {k.lower(): (v or "") for k, v in attrs}

        if tag == "script" and data.get("type", "").strip().lower() == "application/ld+json":
            self.in_json_ld = True
            self.current_json_ld_parts = []
            return

        if "itemscope" in data:
            itemtype = data.get("itemtype", "").strip()
            if itemtype:
                self.microdata_types.append(itemtype)

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
        tag = tag.lower()
        if tag == "script" and self.in_json_ld:
            self.json_ld_blocks.append("".join(self.current_json_ld_parts).strip())
            self.in_json_ld = False
            self.current_json_ld_parts = []
            return

        if tag != "title":
            return

        if self.in_title:
            value = " ".join(
                part.strip() for part in self.current_title_parts if part.strip()
            ).strip()
            self.title_values.append(value)

        self.in_title = False
        self.current_title_parts = []

    def handle_data(self, data: str) -> None:
        if self.in_json_ld:
            self.current_json_ld_parts.append(data)
        elif self.in_title:
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


def _charset_from_header(content_type_header: str | None) -> str | None:
    if not content_type_header:
        return None
    match = _CHARSET_RE.search(content_type_header)
    if not match:
        return None
    charset = match.group(1).strip("\"'")
    return charset or None


def sniff_charset(content: bytes, content_type_header: str | None = None) -> str:
    """Charset detection in the standard (browser-like) priority order -
    deliberately not a statistical/heuristic auto-detector, just the
    explicit signals a well-behaved response provides:

      1. charset from the HTTP Content-Type response header - the most
         authoritative source, since it's a signal from the server
         operator, not from document content a page author might get wrong;
      2. <meta charset="..."> declared in the document itself;
      3. the older <meta http-equiv="Content-Type" content="text/html;
         charset=..."> form;
      4. UTF-8, if nothing was declared anywhere.

    If the HTTP header and an in-document <meta> disagree, the HTTP header
    wins outright - this matches real browser behavior (the transport-level
    signal is treated as more authoritative than document-internal markup)
    and is checked first, so a conflicting <meta> is never even consulted.
    """
    header_charset = _charset_from_header(content_type_header)
    if header_charset:
        return header_charset

    # We don't know the real encoding yet, so this can't decode the bytes
    # "properly" - only far enough to spot an ASCII meta tag. latin-1 maps
    # every byte to exactly one code point and can never raise, so this is
    # a safe, lossless way to read only the ASCII structure of the prefix
    # without needing to already know the encoding. Per the HTML5 spec a
    # charset meta tag must appear within the first 1024 bytes; 4096 is a
    # generous allowance for real-world pages that don't strictly comply.
    prober = MetadataParser()
    try:
        prober.feed(content[:4096].decode("latin-1"))
    except Exception:
        pass
    if prober.charset:
        return prober.charset

    return "utf-8"


def decode_html(content: bytes, content_type_header: str | None = None) -> str:
    """Decode HTML bytes using the detected charset (see sniff_charset).
    Always succeeds: an unknown codec name or a charset declaration that
    doesn't actually match the bytes (a lying/broken page) falls back to
    UTF-8 with replacement characters, exactly like the previous
    unconditional-UTF-8 behavior - a bad declaration must degrade
    gracefully, never raise or crash the audit."""
    charset = sniff_charset(content, content_type_header)
    try:
        return content.decode(charset)
    except (LookupError, UnicodeDecodeError):
        return content.decode("utf-8", errors="replace")
