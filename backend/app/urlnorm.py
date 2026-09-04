"""Safe URL equivalence for canonical analysis.

This is the *single* URL-equivalence function used everywhere canonical logic
needs to decide "are these two URLs the same resource": self-canonical
detection, "several identical vs several different" canonical signals,
HTML-vs-HTTP-``Link`` agreement, and looking a canonical target up in the
audit's result map. Defining it once guarantees those four call sites can
never drift apart.

The rules were agreed explicitly and deliberately conservative - HeadInspect
must not *guess* the site owner's intent:

  * scheme and host are compared case-insensitively (``HTTP`` == ``http``,
    ``Example.COM`` == ``example.com``);
  * the default port for the scheme is dropped (``:80`` for http, ``:443``
    for https), because ``https://x/`` and ``https://x:443/`` are the same
    resource - and ``normalize_public_url`` does *not* itself collapse an
    explicit default port, so this is where it happens;
  * ``path`` and ``query`` are compared **byte-for-byte** - no case folding,
    no trailing-slash normalization: ``/Page/`` and ``/page/``, and ``/foo``
    and ``/foo/``, are treated as potentially different resources;
  * the fragment never participates in equivalence (it is not part of URL
    canonicalization);
  * ``www`` and non-``www`` are **never** collapsed - they stay distinct,
    matching the load-bearing www/non-www semantics used across HeadInspect.
"""

from __future__ import annotations

from urllib.parse import urlsplit

_DEFAULT_PORTS = {"http": 80, "https": 443}


def canonical_equiv_key(url: str) -> str | None:
    """Return a stable comparison key for ``url`` under the safe-equivalence
    rules above, or ``None`` if the URL can't be parsed into an http(s) URL
    with a host (an unusable canonical target we can't reason about)."""
    if not url:
        return None
    try:
        parts = urlsplit(url)
    except ValueError:
        return None

    scheme = (parts.scheme or "").lower()
    if scheme not in _DEFAULT_PORTS:
        return None

    host = (parts.hostname or "").rstrip(".").lower()
    if not host:
        return None

    port = parts.port
    if port is None or port == _DEFAULT_PORTS[scheme]:
        netloc = host
    else:
        netloc = f"{host}:{port}"

    # path/query kept exactly as written; fragment dropped.
    path = parts.path or "/"
    return f"{scheme}://{netloc}{path}?{parts.query}"


def urls_equivalent(a: str, b: str) -> bool:
    """True iff ``a`` and ``b`` denote the same resource under the safe rules.
    Two URLs that both fail to parse are *not* considered equivalent."""
    key_a = canonical_equiv_key(a)
    key_b = canonical_equiv_key(b)
    if key_a is None or key_b is None:
        return False
    return key_a == key_b
