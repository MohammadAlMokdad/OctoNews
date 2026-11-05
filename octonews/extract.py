"""Resolve final article URLs and extract readable article text."""

from __future__ import annotations

import logging

import httpx
import trafilatura

from . import config

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


def fetch_article_text(url: str) -> str:
    """Return the main article text (truncated), or '' if it can't be fetched/extracted."""
    try:
        headers = {"User-Agent": config.USER_AGENT}
        with httpx.Client(headers=headers, timeout=config.HTTP_TIMEOUT, follow_redirects=True) as client:
            resp = client.get(url)
            resp.raise_for_status()
        text = trafilatura.extract(
            resp.text, url=url, include_comments=False, include_tables=False, favor_precision=True
        )
        return (text or "")[: config.ARTICLE_TEXT_CHARS]
    except Exception as exc:
        log.info("Article text unavailable for %s: %s", url, exc)
        return ""
