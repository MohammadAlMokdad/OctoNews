"""Persistent bot state (a single JSON file, committed to the bot-state branch by the workflow)."""

from __future__ import annotations

import json
import os
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path

from . import config

SCHEMA_VERSION = 1


def slot_key_for(day: date, hour: int) -> str:
    return f"{day.isoformat()}@{hour:02d}"


class State:
    """Holds JSON-serializable data only; callers convert to/from domain objects."""

    def __init__(self, data: dict | None = None):
        data = data or {}
        self.seen: dict[str, str] = data.get("seen", {})  # article id -> first-seen ISO time
        self.candidates: dict[str, dict] = data.get("candidates", {})  # article id -> candidate dict
        self.posted: list[dict] = data.get("posted", [])
        self.slots_done: list[str] = data.get("slots_done", [])

    # ---- persistence -------------------------------------------------
    @classmethod
    def load(cls, path: Path) -> "State":
        if not path.exists() or path.stat().st_size == 0:
            return cls()
        return cls(json.loads(path.read_text(encoding="utf-8")))

    def to_dict(self) -> dict:
        return {
            "version": SCHEMA_VERSION,
            "seen": self.seen,
            "candidates": self.candidates,
            "posted": self.posted,
            "slots_done": self.slots_done,
        }

    def save(self, path: Path) -> None:
        """Atomic write so a crash mid-save never leaves a corrupt file."""
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".state-", suffix=".json")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, ensure_ascii=False, indent=1, sort_keys=True)
        os.replace(tmp, path)

    # ---- seen / candidates --------------------------------------------
    def is_seen(self, article_id: str) -> bool:
        return article_id in self.seen

    def mark_seen(self, article_ids, now: datetime) -> None:
        stamp = now.isoformat()
        for aid in article_ids:
            self.seen.setdefault(aid, stamp)

    def add_candidate(self, article_id: str, candidate: dict) -> None:
        self.candidates[article_id] = candidate

    def remove_candidate(self, article_id: str) -> None:
        self.candidates.pop(article_id, None)

    # ---- posting log ---------------------------------------------------
    def record_post(self, entry: dict) -> None:
        """entry must include: id, title, english_title, url, kind ('slot'|'breaking'), posted_at (ISO UTC)."""
        self.posted.append(entry)
        self.remove_candidate(entry["id"])

    def posted_since(self, since: datetime) -> list[dict]:
        return [p for p in self.posted if datetime.fromisoformat(p["posted_at"]) >= since]

    def count_posts_on(self, local_day: date, kind: str | None = None) -> int:
        n = 0
        for p in self.posted:
            if kind and p.get("kind") != kind:
                continue
            if datetime.fromisoformat(p["posted_at"]).astimezone(config.TZ).date() == local_day:
                n += 1
        return n

    def posted_lebanon_on(self, local_day: date) -> bool:
        return any(
            p.get("lebanon_relevance", 0) >= 1
            and datetime.fromisoformat(p["posted_at"]).astimezone(config.TZ).date() == local_day
            for p in self.posted
        )

    # ---- slots ---------------------------------------------------------
    def is_slot_done(self, local_day: date, hour: int) -> bool:
        return slot_key_for(local_day, hour) in self.slots_done

    def count_posts_in_slot(self, local_day: date, hour: int) -> int:
        """Posts already made for this slot (a slot interrupted by an AI outage continues next run)."""
        key = slot_key_for(local_day, hour)
        return sum(1 for p in self.posted if p.get("slot") == key)

    def mark_slot_done(self, local_day: date, hour: int) -> None:
        key = slot_key_for(local_day, hour)
        if key not in self.slots_done:
            self.slots_done.append(key)

    # ---- housekeeping --------------------------------------------------
    def prune(self, now: datetime) -> None:
        seen_cutoff = now - timedelta(days=config.SEEN_RETENTION_DAYS)
        self.seen = {k: v for k, v in self.seen.items() if datetime.fromisoformat(v) >= seen_cutoff}

        cand_cutoff = now - timedelta(hours=config.MAX_ARTICLE_AGE_HOURS)
        posted_ids = {p["id"] for p in self.posted}
        self.candidates = {
            k: v
            for k, v in self.candidates.items()
            if k not in posted_ids and datetime.fromisoformat(v["published"]) >= cand_cutoff
        }

        posted_cutoff = now - timedelta(days=config.POSTED_RETENTION_DAYS)
        self.posted = [p for p in self.posted if datetime.fromisoformat(p["posted_at"]) >= posted_cutoff]

        slot_cutoff = (now.astimezone(config.TZ) - timedelta(days=3)).date().isoformat()
        self.slots_done = [s for s in self.slots_done if s.split("@")[0] >= slot_cutoff]
