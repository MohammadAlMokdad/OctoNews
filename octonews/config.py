"""Settings, constants and source list loading."""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

import yaml

ROOT = Path(__file__).resolve().parent.parent
PROMPTS_DIR = ROOT / "prompts"
SOURCES_FILE = ROOT / "sources.yaml"
DEFAULT_STATE_FILE = ROOT / "data" / "state.json"

TZ = ZoneInfo("Asia/Beirut")

# Posting schedule (Beirut local hours): one story every 2 hours, 08:00-22:00.
SLOT_HOURS = (8, 10, 12, 14, 16, 18, 20, 22)
MAX_POSTS_PER_SLOT = 1
MAX_SLOT_POSTS_PER_DAY = len(SLOT_HOURS)
LEBANON_GUARANTEE_FROM_HOUR = 18  # from this slot on, post a Lebanon story if none went out today
MAX_BREAKING_PER_DAY = 2
QUIET_START_HOUR = 23  # no posting from 23:00 ...
QUIET_END_HOUR = 7  # ... until 07:00

# Freshness / housekeeping.
MAX_ARTICLE_AGE_HOURS = 24
BREAKING_MAX_AGE_HOURS = 3
SEEN_RETENTION_DAYS = 7
POSTED_RETENTION_DAYS = 14
DEDUPE_WINDOW_HOURS = 72
DEDUPE_SIMILARITY = 85  # rapidfuzz token_sort_ratio threshold (0-100)

# Ranking.
MIN_IMPORTANCE_TO_KEEP = 5
MIN_SCORE_TO_POST = 6.0
BREAKING_MIN_IMPORTANCE = 8
# Small boost only: global big-tech news competes on importance; the daily Lebanon story is
# guaranteed separately (LEBANON_GUARANTEE_FROM_HOUR).
LEBANON_BOOST = {0: 0.0, 1: 0.5, 2: 1.0, 3: 1.5}  # by lebanon_relevance level

# AI call sizing (Gemini free tier is rate limited per minute and per day).
SCREEN_BATCH_SIZE = 40
MAX_SCREEN_ITEMS_PER_RUN = 200  # the rest wait for the next hourly run
ARTICLE_TEXT_CHARS = 6000

# Post sizing.
MAX_HEADLINE_CHARS = 140
MAX_SUMMARY_CHARS = 450

# Gemini fallback chains, tried in order (busy or out-of-quota models fall through to the next).
# Free-tier limits seen Nov 2026: 3.6 Flash and 3.8 Flash 5/min, 20/day each; 3.5 Flash Lite 15/min, 500/day.
# Both jobs use 3.5 Flash Lite (owner's choice): ~24-30 screening + <=11 writing calls/day fits its 500/day.
DEFAULT_SCREEN_MODELS = "gemini-3.5-flash-lite,gemini-3.1-flash-lite"
DEFAULT_WRITE_MODELS = "gemini-3.5-flash-lite,gemini-3.1-flash-lite"

USER_AGENT = "Mozilla/5.0 (compatible; OctoNewsBot/1.0; +https://github.com/)"
HTTP_TIMEOUT = 20.0


@dataclass(frozen=True)
class Feed:
    name: str
    url: str
    region: str = "global"  # global | mena | lebanon
    lang: str = "en"
    weight: float = 0.0
    keywords: bool = False


@dataclass(frozen=True)
class Settings:
    feeds: list[Feed]
    gemini_api_key: str = ""
    gemini_screen_models: str = DEFAULT_SCREEN_MODELS
    gemini_write_models: str = DEFAULT_WRITE_MODELS
    channel_id: str = ""  # target WhatsApp Channel, "...@newsletter"
    whatsapp_provider: str = "waha"  # waha | whapi
    waha_url: str = "http://localhost:3000"
    waha_api_key: str = ""
    waha_session: str = "default"
    whapi_token: str = ""
    state_file: Path = DEFAULT_STATE_FILE
    paused_until: datetime | None = None  # no automatic posting before this time (screening continues)


def parse_paused_until(value: str | None) -> datetime | None:
    """ISO date/time; a time without a zone is taken as Beirut time. Empty -> not paused."""
    if not value or not value.strip():
        return None
    dt = datetime.fromisoformat(value.strip())
    return dt if dt.tzinfo else dt.replace(tzinfo=TZ)


def load_dotenv(path: Path = ROOT / ".env") -> None:
    """Minimal .env loader for local runs; never overrides real environment variables."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip('"').strip("'")
        if value and key not in os.environ:
            os.environ[key] = value


def google_news_url(query: str, hl: str = "en-US", gl: str = "US", ceid: str = "US:en") -> str:
    return "https://news.google.com/rss/search?" + urlencode(
        {"q": query, "hl": hl, "gl": gl, "ceid": ceid}
    )


def load_feeds(path: Path = SOURCES_FILE) -> list[Feed]:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    feeds = []
    for item in data.get("feeds", []):
        if "google_news" in item:
            url = google_news_url(**item["google_news"])
        else:
            url = item["url"]
        feeds.append(
            Feed(
                name=item["name"],
                url=url,
                region=item.get("region", "global"),
                lang=item.get("lang", "en"),
                weight=float(item.get("weight", 0)),
                keywords=bool(item.get("keywords", False)),
            )
        )
    return feeds


def _env(name: str, default: str = "") -> str:
    """Read an env var, dropping whitespace and stray byte-order marks (Windows tools can add a BOM
    when piping a secret, which then breaks HTTP headers)."""
    value = os.environ.get(name, "").replace("﻿", "").strip()
    return value or default


def load_settings() -> Settings:
    load_dotenv()
    state_file = _env("OCTONEWS_STATE_FILE")
    return Settings(
        feeds=load_feeds(),
        gemini_api_key=_env("GEMINI_API_KEY"),
        gemini_screen_models=_env("GEMINI_SCREEN_MODELS", DEFAULT_SCREEN_MODELS),
        gemini_write_models=_env("GEMINI_WRITE_MODELS", DEFAULT_WRITE_MODELS),
        channel_id=_env("WHATSAPP_CHANNEL_ID") or _env("WHAPI_CHANNEL_ID"),
        whatsapp_provider=_env("WHATSAPP_PROVIDER", "waha").lower(),
        waha_url=_env("WAHA_URL", "http://localhost:3000"),
        waha_api_key=_env("WAHA_API_KEY"),
        waha_session=_env("WAHA_SESSION", "default"),
        whapi_token=_env("WHAPI_TOKEN"),
        state_file=Path(state_file) if state_file else DEFAULT_STATE_FILE,
        paused_until=parse_paused_until(_env("OCTONEWS_PAUSED_UNTIL")),
    )
