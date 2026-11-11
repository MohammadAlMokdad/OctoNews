"""Scoring, de-duplication and the decisions about what to post when."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from rapidfuzz import fuzz

from . import config
from .feeds import Article
from .state import State

CATEGORIES = ("cybersecurity", "ai", "tech")


@dataclass
class Candidate(Article):
    """An Article plus the AI screening result."""

    english_title: str = ""
    category: str = "tech"  # cybersecurity | ai | tech
    importance: int = 0  # 1-10
    lebanon_relevance: int = 0  # 0 none, 1 MENA region, 2 mentions Lebanon, 3 about Lebanon
    breaking: bool = False
    screened_at: str = ""

    @property
    def is_lebanon(self) -> bool:
        return self.lebanon_relevance >= 2


def score(c: Candidate, now: datetime) -> float:
    hours_old = max(0.0, (now - c.published_dt).total_seconds() / 3600)
    freshness_penalty = min(2.0, hours_old * 0.1)
    return c.importance + config.LEBANON_BOOST.get(c.lebanon_relevance, 0.0) + c.source_weight - freshness_penalty


def is_duplicate(title: str, others: list[str]) -> bool:
    """Near-identical headline check. (Same story, different wording is caught by the AI screen.)

    token_sort_ratio, not token_set_ratio: the latter scores 100 whenever one title's words are a
    subset of another's, which flags unrelated short headlines as duplicates.
    """
    t = title.lower().strip()
    if not t:
        return False
    return any(fuzz.token_sort_ratio(t, o.lower().strip()) >= config.DEDUPE_SIMILARITY for o in others if o)


def known_titles(state: State, now: datetime, limit: int = 120) -> list[str]:
    """English headlines already posted recently or waiting in the queue (for the AI duplicate check)."""
    since = now - timedelta(hours=config.DEDUPE_WINDOW_HOURS)
    titles = [p.get("english_title") or p.get("title", "") for p in state.posted_since(since)]
    titles += [c.get("english_title") or c.get("title", "") for c in state.candidates.values()]
    return [t for t in titles if t][-limit:]


def in_quiet_hours(local_now: datetime) -> bool:
    return local_now.hour >= config.QUIET_START_HOUR or local_now.hour < config.QUIET_END_HOUR


def due_slot(local_now: datetime, state: State) -> int | None:
    """The slot to run now: the latest slot hour <= current hour that hasn't run today.

    Earlier missed slots are skipped on purpose (a late cron run posts once, not three times).
    """
    if in_quiet_hours(local_now):
        return None
    passed = [h for h in config.SLOT_HOURS if h <= local_now.hour]
    if not passed:
        return None
    latest = passed[-1]
    return None if state.is_slot_done(local_now.date(), latest) else latest


def _recent_titles(state: State, now: datetime) -> list[str]:
    since = now - timedelta(hours=config.DEDUPE_WINDOW_HOURS)
    titles = []
    for p in state.posted_since(since):
        titles.append(p.get("english_title") or "")
        titles.append(p.get("title") or "")
    return titles


def shortlist(candidates: list[Candidate], now: datetime, size: int = 15, lebanon_extra: int = 3) -> list[Candidate]:
    """Best candidates (by score) for the AI duplicate check, plus a few Lebanon/MENA stories so the
    evening Lebanon guarantee still has options after the check."""
    ranked = sorted(candidates, key=lambda c: score(c, now), reverse=True)
    picked = ranked[:size]
    picked += [c for c in ranked[size:] if c.lebanon_relevance >= 1][:lebanon_extra]
    return picked


def recent_post_titles(state: State, now: datetime) -> list[str]:
    since = now - timedelta(hours=config.DEDUPE_WINDOW_HOURS)
    return [p.get("english_title") or p.get("title", "") for p in state.posted_since(since)]


def select_for_slot(
    candidates: list[Candidate], state: State, now: datetime, slot_hour: int
) -> list[Candidate]:
    local_day = now.astimezone(config.TZ).date()
    remaining_today = config.MAX_SLOT_POSTS_PER_DAY - state.count_posts_on(local_day, "slot")
    remaining_in_slot = config.MAX_POSTS_PER_SLOT - state.count_posts_in_slot(local_day, slot_hour)
    limit = max(0, min(remaining_in_slot, remaining_today))
    if limit == 0:
        return []

    taken = _recent_titles(state, now)
    ranked = sorted(candidates, key=lambda c: score(c, now), reverse=True)

    picks: list[Candidate] = []
    for c in ranked:
        if len(picks) >= limit:
            break
        if score(c, now) < config.MIN_SCORE_TO_POST:
            break
        if is_duplicate(c.english_title or c.title, taken):
            continue
        picks.append(c)
        taken += [c.english_title, c.title]

    # Guarantee: at least one Lebanon/MENA story per day when one exists (checked at the last slot).
    is_last_slot = slot_hour == config.SLOT_HOURS[-1]
    if is_last_slot and not state.posted_lebanon_on(local_day) and not any(p.lebanon_relevance >= 1 for p in picks):
        local = [c for c in ranked if c.lebanon_relevance >= 1 and c not in picks]
        for c in local:
            if not is_duplicate(c.english_title or c.title, _recent_titles(state, now)):
                if len(picks) >= limit:
                    picks[-1] = c
                else:
                    picks.append(c)
                break
    return picks


def select_breaking(candidates: list[Candidate], state: State, now: datetime) -> Candidate | None:
    local_now = now.astimezone(config.TZ)
    if in_quiet_hours(local_now):
        return None
    if state.count_posts_on(local_now.date(), "breaking") >= config.MAX_BREAKING_PER_DAY:
        return None
    max_age = timedelta(hours=config.BREAKING_MAX_AGE_HOURS)
    eligible = [
        c
        for c in candidates
        if c.breaking
        and c.is_lebanon
        and c.importance >= config.BREAKING_MIN_IMPORTANCE
        and now - c.published_dt <= max_age
    ]
    taken = _recent_titles(state, now)
    for c in sorted(eligible, key=lambda c: score(c, now), reverse=True):
        if not is_duplicate(c.english_title or c.title, taken):
            return c
    return None
