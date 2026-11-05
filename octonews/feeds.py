"""Fetch RSS/Atom feeds and normalize entries into Article objects."""

from __future__ import annotations

import calendar
import hashlib
import html
import logging
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import feedparser
import httpx

from . import config
from .config import Feed

log = logging.getLogger(__name__)

MAX_ITEMS_PER_FEED = 30
SNIPPET_CHARS = 500

_TRACKING_PARAMS = re.compile(r"^(utm_|fbclid$|gclid$|mc_cid$|mc_eid$|ref$|ocid$|cmpid$)", re.I)

# Keyword prefilter for general-news sources (English, Arabic, French).
# Short ASCII terms use word boundaries so "AI" does not match "said".
_KEYWORD_WORDS = [
    "AI", "A.I.", "IA", "5G", "4G", "app", "apps", "data", "chip", "chips", "robot", "robots", "drone",
    "drones", "Ogero", "Alfa", "Touch", "Starlink", "OpenAI", "ChatGPT", "Gemini", "Claude",
]
_KEYWORD_SUBSTRINGS = [
    "cyber", "hack", "breach", "ransomware", "malware", "phishing", "spyware", "pegasus", "leak",
    "artificial intelligence", "machine learning", "technolog", "tech ", "startup", "start-up",
    "internet", "telecom", "digital", "software", "online", "satellite", "semiconductor",
    "fiber", "fibre", "smartphone", "privacy", "surveillance", "deepfake", "crypto", "blockchain",
    "electronic", "computer",
    # French
    "piratage", "pirat", "intelligence artificielle", "numérique", "données", "informatique",
    # Arabic
    "سيبران", "اختراق", "قرصن", "هاكر", "ذكاء اصطناعي", "الذكاء الاصطناعي", "تكنولوج", "تقني",
    "الإنترنت", "الانترنت", "إنترنت", "انترنت", "رقمي", "رقمية", "الاتصالات", "أوجيرو", "اوجيرو",
    "تطبيق", "بيانات", "إلكتروني", "الكتروني", "برمج", "تجسس", "الخلوي", "شركة ناشئة", "شركات ناشئة",
]
_KEYWORD_RE = re.compile(
    r"(?:\b(?:" + "|".join(re.escape(w) for w in _KEYWORD_WORDS) + r")\b)"
    + "|" + "|".join(re.escape(s) for s in _KEYWORD_SUBSTRINGS),
    re.IGNORECASE,
)
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


@dataclass
class Article:
    id: str
    title: str
    url: str
    source: str
    region: str
    lang: str
    published: str  # ISO-8601 UTC
    snippet: str
    source_weight: float = 0.0

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Article":
        fields = cls.__dataclass_fields__
        return cls(**{k: v for k, v in d.items() if k in fields})

    @property
    def published_dt(self) -> datetime:
        return datetime.fromisoformat(self.published)


def normalize_url(url: str) -> str:
    """Strip tracking params and fragments so the same article gets the same id."""
    parts = urlsplit(url.strip())
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if not _TRACKING_PARAMS.match(k)]
    host = parts.netloc.lower()
    path = parts.path.rstrip("/") or "/"
    return urlunsplit((parts.scheme.lower() or "https", host, path, urlencode(query), ""))


def article_id(url: str) -> str:
    return hashlib.sha1(normalize_url(url).encode("utf-8")).hexdigest()[:16]


def clean_text(raw: str | None, limit: int = SNIPPET_CHARS) -> str:
    if not raw:
        return ""
    text = html.unescape(_TAG_RE.sub(" ", raw))
    text = _WS_RE.sub(" ", text).strip()
    return text[:limit]


def matches_tech_keywords(text: str) -> bool:
    return bool(_KEYWORD_RE.search(text))


def _entry_time(entry, fallback: datetime) -> datetime:
    for key in ("published_parsed", "updated_parsed"):
        value = entry.get(key)
        if value:
            return datetime.fromtimestamp(calendar.timegm(value), tz=timezone.utc)
    return fallback


def parse_feed(feed: Feed, content: bytes, now: datetime) -> list[Article]:
    parsed = feedparser.parse(content)
    articles = []
    for entry in parsed.entries[:MAX_ITEMS_PER_FEED]:
        link = entry.get("link")
        title = clean_text(entry.get("title"), 300)
        if not link or not title:
            continue
        snippet = clean_text(entry.get("summary") or entry.get("description"))
        # Google News puts "Title - Publisher" in the title and repeats it in the summary.
        if "news.google.com" in link:
            snippet = ""
        if feed.keywords and not matches_tech_keywords(f"{title} {snippet}"):
            continue
        published = min(_entry_time(entry, now), now)  # clamp future-dated items
        articles.append(
            Article(
                id=article_id(link),
                title=title,
                url=link,
                source=feed.name,
                region=feed.region,
                lang=feed.lang,
                published=published.isoformat(),
                snippet=snippet,
                source_weight=feed.weight,
            )
        )
    return articles


def _fetch_one(client: httpx.Client, feed: Feed, now: datetime) -> tuple[Feed, list[Article], str | None]:
    try:
        resp = client.get(feed.url)
        resp.raise_for_status()
        return feed, parse_feed(feed, resp.content, now), None
    except Exception as exc:  # one broken feed must not stop the run
        return feed, [], f"{type(exc).__name__}: {exc}"


def fetch_all(
    feeds: list[Feed], now: datetime | None = None
) -> tuple[list[Article], dict[str, tuple[int, str | None]]]:
    """Fetch every feed in parallel. Returns (articles, {feed name: (item count, error or None)})."""
    now = now or datetime.now(timezone.utc)
    headers = {"User-Agent": config.USER_AGENT, "Accept": "application/rss+xml, application/xml, text/xml, */*"}
    results: dict[str, tuple[int, str | None]] = {}
    articles: dict[str, Article] = {}
    with httpx.Client(headers=headers, timeout=config.HTTP_TIMEOUT, follow_redirects=True) as client:
        with ThreadPoolExecutor(max_workers=8) as pool:
            for feed, items, error in pool.map(lambda f: _fetch_one(client, f, now), feeds):
                results[feed.name] = (len(items), error)
                if error:
                    log.warning("Feed failed: %s (%s)", feed.name, error)
                for a in items:
                    articles.setdefault(a.id, a)
    return list(articles.values()), results
