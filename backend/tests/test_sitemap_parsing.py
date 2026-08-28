"""Item 2 (image:loc/video:loc must not become pages) and item 5 (gzip
sitemap support) - see the plan's automated-test checklist items 1, 2, 3,
4, 5, 6.
"""
from __future__ import annotations

import gzip

import pytest
from fastapi import HTTPException

from app import sitemap


def test_image_loc_does_not_become_a_page():
    xml = b"""<?xml version="1.0" encoding="UTF-8"?>
    <urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"
            xmlns:image="http://www.google.com/schemas/sitemap-image/1.1">
      <url>
        <loc>https://example.ru/page1</loc>
        <image:image><image:loc>https://example.ru/img1.jpg</image:loc></image:image>
      </url>
      <url>
        <loc>https://example.ru/page2</loc>
        <image:image><image:loc>https://example.ru/img2.jpg</image:loc></image:image>
        <image:image><image:loc>https://example.ru/img3.jpg</image:loc></image:image>
      </url>
    </urlset>"""
    kind, locs = sitemap._parse_sitemap_xml(xml)
    assert kind == "urlset"
    assert locs == ["https://example.ru/page1", "https://example.ru/page2"]
    assert not any("img" in u for u in locs)


def test_video_loc_does_not_become_a_page():
    xml = b"""<?xml version="1.0" encoding="UTF-8"?>
    <urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"
            xmlns:video="http://www.google.com/schemas/sitemap-video/1.1">
      <url>
        <loc>https://example.ru/watch</loc>
        <video:video>
          <video:loc>https://example.ru/video.mp4</video:loc>
          <video:title>Demo</video:title>
        </video:video>
      </url>
    </urlset>"""
    kind, locs = sitemap._parse_sitemap_xml(xml)
    assert locs == ["https://example.ru/watch"]


def test_plain_urlset_keeps_working():
    xml = b"""<?xml version="1.0"?>
    <urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
      <url><loc>https://example.ru/a</loc></url>
      <url><loc>https://example.ru/b</loc></url>
    </urlset>"""
    kind, locs = sitemap._parse_sitemap_xml(xml)
    assert kind == "urlset"
    assert locs == ["https://example.ru/a", "https://example.ru/b"]


def test_sitemap_index_keeps_working():
    xml = b"""<?xml version="1.0"?>
    <sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
      <sitemap><loc>https://example.ru/sitemap-1.xml</loc></sitemap>
      <sitemap><loc>https://example.ru/sitemap-2.xml</loc></sitemap>
    </sitemapindex>"""
    kind, locs = sitemap._parse_sitemap_xml(xml)
    assert kind == "index"
    assert locs == ["https://example.ru/sitemap-1.xml", "https://example.ru/sitemap-2.xml"]


def test_gzip_sitemap_is_transparently_decompressed():
    plain = b"""<?xml version="1.0"?>
    <urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
      <url><loc>https://example.ru/a</loc></url>
      <url><loc>https://example.ru/b</loc></url>
    </urlset>"""
    kind, locs = sitemap._parse_sitemap_xml(gzip.compress(plain))
    assert locs == ["https://example.ru/a", "https://example.ru/b"]


def test_corrupted_gzip_raises_an_explanatory_error_not_a_silent_fallback():
    corrupted = b"\x1f\x8b" + b"\x00" * 20
    with pytest.raises(HTTPException) as exc_info:
        sitemap._parse_sitemap_xml(corrupted)
    assert "gzip" in exc_info.value.detail.lower()


def test_gzip_bomb_is_rejected():
    huge_xml = (
        b'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        + b"<url><loc>https://example.ru/x</loc></url>" * 2_000_000
        + b"</urlset>"
    )
    assert len(huge_xml) > sitemap.MAX_SITEMAP_DECOMPRESSED_BYTES
    with pytest.raises(HTTPException) as exc_info:
        sitemap._parse_sitemap_xml(gzip.compress(huge_xml))
    assert "too large" in exc_info.value.detail.lower()


def test_non_gzip_bytes_pass_through_unchanged():
    plain = b"<urlset></urlset>"
    assert sitemap._maybe_decompress_gzip(plain) == plain


def test_unsupported_root_element_still_rejected():
    with pytest.raises(HTTPException):
        sitemap._parse_sitemap_xml(b"<rss><channel></channel></rss>")


def test_invalid_xml_still_rejected():
    with pytest.raises(HTTPException):
        sitemap._parse_sitemap_xml(b"not xml at all <<<")
