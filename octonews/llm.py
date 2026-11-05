"""The only module that talks to the AI provider (Google Gemini, free tier).

To switch providers later (e.g. Claude), re-implement `Editor.screen_batch` and `Editor.write_post`
with the same inputs/outputs; nothing else in the project needs to change.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Literal, TypeVar

from google import genai
from google.genai import errors, types
from pydantic import BaseModel, ValidationError

from . import config
from .feeds import Article
from .formatter import PostContent

log = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)

# Free tier (Nov 2026): Flash models allow 5 requests/minute and 20/day; Flash Lite 15/minute and 500/day.
# 13s spacing keeps every model under 5/minute.
MIN_SECONDS_BETWEEN_CALLS = 13.0
TRANSIENT_CODES = {500, 502, 503, 504}  # "high demand" etc.: retry
SKIP_FOR_RUN_CODES = {404, 429}  # retired model / out of quota: use the next model for the rest of the run
ATTEMPTS_PER_MODEL = 2
CHAIN_ROUNDS = 4  # free-tier 503s often last a few minutes; Actions time is free on a public repo
RETRY_DELAY_SECONDS = 5
ROUND_DELAY_SECONDS = 30


class ScreenItem(BaseModel):
    idx: int
    english_title: str
    category: Literal["cybersecurity", "ai", "tech", "other"]
    importance: int
    lebanon_relevance: int
    breaking: bool
    duplicate_of: int
    already_covered: bool


class ScreenResult(BaseModel):
    items: list[ScreenItem]


class WriteResult(BaseModel):
    skip: bool
    skip_reason: str
    ar_headline: str
    ar_summary: str
    en_headline: str
    en_summary: str
    hashtags: list[str]


def _prompt(name: str) -> str:
    return (config.PROMPTS_DIR / f"{name}.md").read_text(encoding="utf-8")


def _chain(models: str) -> list[str]:
    chain = [m.strip() for m in models.split(",") if m.strip()]
    if not chain:
        raise RuntimeError("No Gemini model configured")
    return chain


class Editor:
    def __init__(self, api_key: str, screen_models: str, write_models: str):
        """Each argument is a comma-separated fallback chain, tried in order.

        Screening runs every hour, so it uses a high-quota model (Flash Lite, 500/day). Writing happens
        ~10 times a day, so it can use the better but scarce Flash models (20/day each).
        """
        if not api_key:
            raise RuntimeError("GEMINI_API_KEY is not set")
        self.client = genai.Client(api_key=api_key)
        self.screen_models = _chain(screen_models)
        self.write_models = _chain(write_models)
        self._down: set[str] = set()  # models out of quota / retired; skipped for the rest of the run
        self._last_call = 0.0

    def _call(self, model: str, system_prompt: str, user_text: str, schema: type[T]) -> T:
        wait = MIN_SECONDS_BETWEEN_CALLS - (time.monotonic() - self._last_call)
        if wait > 0:
            time.sleep(wait)
        self._last_call = time.monotonic()
        resp = self.client.models.generate_content(
            model=model,
            contents=user_text,
            config=types.GenerateContentConfig(
                system_instruction=system_prompt,
                response_mime_type="application/json",
                response_schema=schema,
            ),
        )
        if isinstance(resp.parsed, schema):
            return resp.parsed
        return schema.model_validate_json(resp.text or "")

    def _generate(self, models: list[str], system_prompt: str, user_text: str, schema: type[T]) -> T:
        """Try each model in the chain, twice over. Free-tier 503s ("high demand") come and go from one
        request to the next, so a busy model is retried later; only models that are out of quota (429)
        or retired (404) are skipped for the rest of the run."""
        last_exc: Exception | None = None
        for round_no in range(CHAIN_ROUNDS):
            available = [m for m in models if m not in self._down]
            if not available:
                break
            if round_no:
                time.sleep(ROUND_DELAY_SECONDS)
            for model in available:
                for _ in range(ATTEMPTS_PER_MODEL):
                    try:
                        return self._call(model, system_prompt, user_text, schema)
                    except errors.APIError as exc:
                        last_exc = exc
                        if exc.code in SKIP_FOR_RUN_CODES:
                            log.warning("%s: %s, skipping it for this run", model, exc.code)
                            self._down.add(model)
                            break
                        if exc.code in TRANSIENT_CODES:
                            log.info("%s: busy (%s)", model, exc.code)
                            time.sleep(RETRY_DELAY_SECONDS)
                            continue
                        raise
                    except ValidationError as exc:  # malformed JSON from the model
                        last_exc = exc
                        log.warning("%s: bad output (%s)", model, str(exc)[:200])
        raise last_exc or RuntimeError("all Gemini models failed")

    def screen_batch(self, articles: list[Article], known_titles: list[str] | None = None) -> dict[int, ScreenItem]:
        """Classify a batch. Returns {index in `articles`: ScreenItem}; missing indexes = not classified.

        `known_titles` are stories already posted or queued; items covering them get already_covered=true.
        """
        payload = {
            "already_known_stories": known_titles or [],
            "items": [
                {
                    "idx": i,
                    "source": a.source,
                    "lang": a.lang,
                    "published": a.published,
                    "title": a.title,
                    "snippet": a.snippet[:300],
                }
                for i, a in enumerate(articles)
            ],
        }
        result = self._generate(
            self.screen_models, _prompt("screen"), json.dumps(payload, ensure_ascii=False), ScreenResult
        )
        out = {}
        for item in result.items:
            if 0 <= item.idx < len(articles):
                item.importance = max(1, min(10, item.importance))
                item.lebanon_relevance = max(0, min(3, item.lebanon_relevance))
                out[item.idx] = item
        return out

    def write_post(self, article: Article, article_text: str) -> tuple[PostContent | None, str]:
        """Returns (content, '') or (None, reason) when the model decides to skip."""
        payload = {
            "title": article.title,
            "source": article.source,
            "published": article.published,
            "snippet": article.snippet,
            "article_text": article_text,
        }
        r = self._generate(self.write_models, _prompt("write"), json.dumps(payload, ensure_ascii=False), WriteResult)
        if r.skip:
            return None, r.skip_reason or "model skipped"
        return (
            PostContent(
                ar_headline=r.ar_headline,
                ar_summary=r.ar_summary,
                en_headline=r.en_headline,
                en_summary=r.en_summary,
                hashtags=list(r.hashtags)[:3],
            ),
            "",
        )


def list_models(api_key: str) -> list[str]:
    client = genai.Client(api_key=api_key)
    names = []
    for m in client.models.list():
        actions = getattr(m, "supported_actions", None) or []
        if "generateContent" in actions:
            names.append(m.name.removeprefix("models/"))
    return sorted(names)
