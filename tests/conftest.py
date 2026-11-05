import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from octonews.rank import Candidate  # noqa: E402

# 2026-11-05 06:07 UTC == 08:07 in Beirut (UTC+2 in winter): the 08:00 slot is due.
NOW = datetime(2026, 11, 5, 6, 7, tzinfo=timezone.utc)


HEADLINES = [
    "Microsoft patches Exchange zero-day exploited by hackers",
    "OpenAI unveils faster reasoning model for developers",
    "Ransomware gang leaks data from German hospital chain",
    "Apple delays smart home display until next spring",
    "EU fines telecom operator over location data sharing",
    "Nvidia reports record revenue on data center demand",
    "Phishing campaign impersonates banks across the Gulf",
    "Starlink expands satellite internet service to new markets",
]


def make_candidate(n: int, *, hours_old: float = 1, importance: int = 7, lebanon: int = 0,
                   category: str = "cybersecurity", breaking: bool = False, title: str | None = None) -> Candidate:
    title = title or (HEADLINES[n] if n < len(HEADLINES) else f"Lebanon ministry launches e-government portal {n}")
    return Candidate(
        id=f"id{n}",
        title=title,
        url=f"https://example.com/{n}",
        source="Example",
        region="global",
        lang="en",
        published=(NOW - timedelta(hours=hours_old)).isoformat(),
        snippet="snippet",
        english_title=title,
        category=category,
        importance=importance,
        lebanon_relevance=lebanon,
        breaking=breaking,
        screened_at=NOW.isoformat(),
    )


@pytest.fixture
def now():
    return NOW
