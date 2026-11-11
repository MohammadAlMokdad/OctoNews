"""Resolve final article URLs, shorten them, and extract readable article text."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from urllib.parse import unquote, urlsplit, urlunsplit

import httpx
import trafilatura
from rapidfuzz import fuzz

from . import config
from .feeds import normalize_url

log = logging.getLogger(__name__)


def resolve_url(url: str) -> str:
    """Google News RSS links point at news.google.com; turn them into the publisher's URL."""
    if "news.google.com" not in url:
        return url
    try:
        from googlenewsdecoder import gnewsdecoder

        result = gnewsdecoder(url, timeout=15)
        ok = isinstance(result, dict) and (result.get("success") or result.get("status"))
        if ok and result.get("decoded_url"):
            return result["decoded_url"]
        log.warning("Could not decode Google News URL: %s", result)
    except Exception as exc:
        log.warning("Google News URL decode failed: %s", exc)
    return url  # the Google News link still redirects readers to the article


def _client() -> httpx.Client:
    return httpx.Client(
        headers={"User-Agent": config.USER_AGENT}, timeout=config.HTTP_TIMEOUT, follow_redirects=True
    )


def fetch_article_text(url: str) -> str:
    """Return the main article text (truncated), or '' if it can't be fetched/extracted."""
    try:
        with _client() as client:
            resp = client.get(url)
            resp.raise_for_status()
        text = trafilatura.extract(
            resp.text, url=url, include_comments=False, include_tables=False, favor_precision=True
        )
        return (text or "")[: config.ARTICLE_TEXT_CHARS]
    except Exception as exc:
        log.info("Article text unavailable for %s: %s", url, exc)
        return ""


# ---------------------------------------------------------------- short links


def _non_ascii(segment: str) -> bool:
    return any(ord(ch) > 127 for ch in unquote(segment))


def short_candidates(url: str) -> list[str]:
    """Shorter forms of a URL whose path ends in a non-Latin slug (e.g. Arabic), most natural first.

    .../news/lebanon/1593444/<arabic-slug> ->
      .../news/lebanon/1593444/, .../news/lebanon/1593444, .../news/1593444, .../1593444
    Sites differ in which form still opens the article, so each one is verified before use.
    Returns [] when there is no slug to drop.
    """
    parts = urlsplit(url)
    segments = parts.path.split("/")
    dropped = False
    while segments and (segments[-1] == "" or _non_ascii(segments[-1])):
        dropped = dropped or segments[-1] != ""
        segments.pop()
    kept = [s for s in segments if s]
    if not dropped or not kept:
        return []

    def make(path: str) -> str:
        return urlunsplit((parts.scheme, parts.netloc, path, parts.query, ""))

    out = [make("/" + "/".join(kept) + "/"), make("/" + "/".join(kept))]
    if kept[-1].isdigit():  # numeric article id: many sites route on the id alone
        if len(kept) >= 3:
            out.append(make(f"/{kept[0]}/{kept[-1]}"))
        if len(kept) >= 2:
            out.append(make(f"/{kept[-1]}"))
    return [u for u in dict.fromkeys(out) if not any(_non_ascii(s) for s in urlsplit(u).path.split("/"))]


@dataclass
class _Page:
    ok: bool
    final_url: str = ""
    title: str = ""
    canonical: str = ""


def _page(client: httpx.Client, url: str) -> _Page:
    resp = client.get(url)
    if resp.status_code != 200:
        return _Page(False)
    meta = trafilatura.extract_metadata(resp.text, default_url=str(resp.url))
    return _Page(True, str(resp.url), (meta.title or "") if meta else "", (meta.url or "") if meta else "")


def _same(a: str, b: str) -> bool:
    return bool(a and b) and unquote(normalize_url(a)) == unquote(normalize_url(b))


def _same_article(original: _Page, short: _Page, url: str, headline: str) -> bool:
    titles_match = (
        bool(original.title and short.title)
        and fuzz.ratio(original.title.lower(), short.title.lower()) >= 90
        and (not headline or fuzz.partial_ratio(headline.lower(), short.title.lower()) >= 70)
    )
    return (
        _same(short.final_url, url)  # the short link redirects to the article
        or _same(short.canonical, original.canonical)
        or titles_match
    )


def shorten_url(url: str, headline: str = "", client: httpx.Client | None = None) -> str:
    """Return the slug-less URL if it opens the same article, else the original URL unchanged.

    `headline` (the story's own title) guards against sites that give every page the same generic
    <title>: a title match only counts if it also matches the headline.
    """
    candidates = short_candidates(url)
    if not candidates:
        return url
    own = client is None
    client = client or _client()
    try:
        original = _page(client, url)
        if not original.ok:
            return url
        for candidate in candidates:
            short = _page(client, candidate)
            if short.ok and _same_article(original, short, url, headline):
                return candidate
        return url
    except Exception as exc:
        log.info("Could not shorten %s: %s", url, exc)
        return url
    finally:
        if own:
            client.close()
