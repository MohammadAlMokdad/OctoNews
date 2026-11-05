"""Builds the final WhatsApp Channel post text. Deterministic: the AI only supplies text fields."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from urllib.parse import quote

from . import config
from .rank import Candidate

CATEGORY_EMOJI = {"cybersecurity": "🛡️", "ai": "🤖", "tech": "💡"}
CATEGORY_TAG = {"cybersecurity": "Cybersecurity", "ai": "AI", "tech": "Tech"}
MAX_HASHTAGS = 5

_ARABIC_RE = re.compile(r"[؀-ۿ]")
_WA_MARKUP_RE = re.compile(r"[*_~`]")
_WS_RE = re.compile(r"\s+")
_TAG_CLEAN_RE = re.compile(r"[^\w]", re.UNICODE)


@dataclass
class PostContent:
    ar_headline: str
    ar_summary: str
    en_headline: str
    en_summary: str
    hashtags: list[str] = field(default_factory=list)


def _clean(text: str) -> str:
    """Remove WhatsApp markup characters (they would break bold/italic) and collapse whitespace."""
    return _WS_RE.sub(" ", _WA_MARKUP_RE.sub("", text or "")).strip()


def safe_url(url: str) -> str:
    """Percent-encode non-ASCII characters (e.g. Arabic slugs) so WhatsApp always makes the link clickable."""
    return quote(url.strip(), safe=":/?#[]@!$&'()*+,;=%~")


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    cut = text[: limit - 1]
    if " " in cut:
        cut = cut[: cut.rfind(" ")]
    return cut.rstrip(" ,.;:-–—،") + "…"


def validate(content: PostContent) -> list[str]:
    """Return a list of problems; empty means the content is usable."""
    problems = []
    for name in ("ar_headline", "ar_summary", "en_headline", "en_summary"):
        value = _clean(getattr(content, name))
        if not value:
            problems.append(f"{name} is empty")
        limit = config.MAX_HEADLINE_CHARS if "headline" in name else config.MAX_SUMMARY_CHARS
        if len(value) > limit * 2:
            problems.append(f"{name} is far too long ({len(value)} chars)")
    for name in ("ar_headline", "ar_summary"):
        value = getattr(content, name) or ""
        if value and not _ARABIC_RE.search(value):
            problems.append(f"{name} is not in Arabic")
    return problems


# Tags only the code decides on (the AI must not add e.g. #Lebanon to a non-Lebanon story).
RESERVED_TAGS = {"lebanon", "لبنان", "tech", "technology", "ai", "cybersecurity", "news", "breaking"}


def _tag(raw: str) -> str:
    return _TAG_CLEAN_RE.sub("", (raw or "").lstrip("#"))


def _hashtags(c: Candidate, extra: list[str]) -> list[str]:
    tags = [CATEGORY_TAG.get(c.category, "Tech")]
    if c.is_lebanon:
        tags += ["Lebanon", "لبنان"]
    tags += [t for t in extra if _tag(t).lower() not in RESERVED_TAGS]
    out, seen = [], set()
    for t in tags:
        t = _tag(t)
        if t and t.lower() not in seen:
            seen.add(t.lower())
            out.append("#" + t)
    return out[:MAX_HASHTAGS]


def build_message(c: Candidate, content: PostContent, url: str, breaking: bool = False) -> str:
    ar_head = _truncate(_clean(content.ar_headline), config.MAX_HEADLINE_CHARS)
    ar_sum = _truncate(_clean(content.ar_summary), config.MAX_SUMMARY_CHARS)
    en_head = _truncate(_clean(content.en_headline), config.MAX_HEADLINE_CHARS)
    en_sum = _truncate(_clean(content.en_summary), config.MAX_SUMMARY_CHARS)

    icons = ("🇱🇧 " if c.is_lebanon else "") + CATEGORY_EMOJI.get(c.category, "💡")
    lines = []
    if breaking:
        lines.append("🚨 *عاجل | Breaking*")
    lines += [
        f"{icons} *{ar_head}*",
        ar_sum,
        "",
        f"*{en_head}*",
        en_sum,
        "",
        f"🔗 {safe_url(url)}",
        " ".join(_hashtags(c, content.hashtags)),
    ]
    return "\n".join(lines).strip()
