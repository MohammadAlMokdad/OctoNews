from datetime import datetime, timedelta

from conftest import make_candidate

from octonews import config, rank
from octonews.state import State

TZ = config.TZ


def local(h, m=7):
    return datetime(2026, 11, 5, h, m, tzinfo=TZ)


def test_lebanon_boost_outranks_similar_global_story(now):
    global_story = make_candidate(1, importance=7, lebanon=0)
    lebanon_story = make_candidate(2, importance=6, lebanon=3)
    assert rank.score(lebanon_story, now) > rank.score(global_story, now)


def test_freshness_penalty_is_capped(now):
    old = make_candidate(1, hours_old=100, importance=8)
    assert rank.score(old, now) == 8 - 2.0


def test_duplicate_detection():
    assert rank.is_duplicate("Microsoft patches zero-day exploited in the wild",
                             ["microsoft patches zero-day exploited in the wild, update now"])
    assert not rank.is_duplicate("OpenAI releases new model", ["Ransomware hits hospital in Germany"])
    # A short headline whose words are a subset of a longer one is NOT a duplicate.
    assert not rank.is_duplicate("Apple", ["Apple releases iOS update fixing exploited flaw"])


def test_shortlist_keeps_best_plus_some_lebanon(now):
    cands = [make_candidate(i, importance=9) for i in range(6)]
    leb = make_candidate(50, importance=5, lebanon=3)
    picked = rank.shortlist(cands + [leb], now, size=4, lebanon_extra=2)
    assert len(picked) == 5 and picked[-1] is leb


def test_known_titles_includes_posted_and_queued(now):
    s = State()
    c = make_candidate(1)
    s.add_candidate(c.id, c.to_dict())
    s.record_post({"id": "p", "title": "t", "english_title": "Posted story", "url": "u",
                   "kind": "slot", "posted_at": now.isoformat()})
    assert rank.known_titles(s, now) == ["Posted story", c.english_title]


def test_due_slot_basic_and_quiet_hours():
    s = State()
    assert rank.due_slot(local(7), s) is None  # quiet until 08:00
    assert rank.due_slot(local(8), s) == 8
    assert rank.due_slot(local(9), s) == 8  # run came late, slot still due
    assert rank.due_slot(local(11), s) == 10  # a missed slot is skipped, not double-posted
    assert rank.due_slot(local(22), s) == 22
    assert rank.due_slot(local(23), s) == 22  # 22:00 still allowed to catch up before midnight
    assert rank.due_slot(local(0, 0), s) == 0  # midnight post
    assert rank.due_slot(local(0, 30), s) is None  # quiet from 00:20
    assert rank.due_slot(local(3), s) is None


def test_due_slot_not_repeated_once_done():
    s = State()
    s.mark_slot_done(local(8).date(), 8)
    assert rank.due_slot(local(9), s) is None
    assert rank.due_slot(local(10), s) == 10


def test_select_for_slot_respects_limits_threshold_and_dedupe(now):
    cands = [make_candidate(i, importance=9 - i) for i in range(6)]
    cands.append(make_candidate(99, importance=9, title=cands[0].title + " today"))  # duplicate of #0
    picks = rank.select_for_slot(cands, State(), now, 8)
    assert len(picks) == config.MAX_POSTS_PER_SLOT
    titles = [p.english_title for p in picks]
    assert sum("Exchange zero-day" in t for t in titles) == 1
    assert all(rank.score(p, now) >= config.MIN_SCORE_TO_POST for p in picks)


def test_select_for_slot_skips_already_posted_story(now):
    s = State()
    c = make_candidate(1, importance=9)
    s.record_post({"id": "old", "title": c.title, "english_title": c.title, "url": "u",
                   "kind": "slot", "posted_at": (now - timedelta(hours=5)).isoformat()})
    assert rank.select_for_slot([c], s, now, 8) == []


def test_select_for_slot_daily_cap(now):
    s = State()
    for i in range(config.MAX_SLOT_POSTS_PER_DAY):
        s.record_post({"id": f"p{i}", "title": f"t{i}", "english_title": f"posted {i}", "url": "u",
                       "kind": "slot", "posted_at": now.isoformat()})
    assert rank.select_for_slot([make_candidate(1, importance=9)], s, now, 13) == []


def test_evening_slot_guarantees_a_lebanon_story(now):
    evening = now.replace(hour=16)  # 18:07 Beirut
    cands = [make_candidate(i, importance=9) for i in range(3)]
    leb = make_candidate(50, importance=5, lebanon=3, hours_old=2)
    picks = rank.select_for_slot(cands + [leb], State(), evening, config.LEBANON_GUARANTEE_FROM_HOUR)
    assert rank.select_for_slot(cands + [leb], State(), now, 8)[0] is not leb  # morning: best story wins
    assert leb in picks
    assert len(picks) == config.MAX_POSTS_PER_SLOT


def test_breaking_requires_lebanon_fresh_important(now):
    s = State()
    good = make_candidate(1, importance=9, lebanon=3, breaking=True, hours_old=1)
    global_breaking = make_candidate(2, importance=10, lebanon=0, breaking=True)
    stale = make_candidate(3, importance=9, lebanon=3, breaking=True, hours_old=5)
    assert rank.select_breaking([global_breaking, stale], s, now) is None
    assert rank.select_breaking([global_breaking, stale, good], s, now) == good


def test_breaking_daily_cap_and_quiet_hours(now):
    s = State()
    good = make_candidate(1, importance=9, lebanon=3, breaking=True)
    night = now.replace(hour=23)  # 01:07 Beirut, quiet
    assert rank.select_breaking([good], s, night) is None
    for i in range(config.MAX_BREAKING_PER_DAY):
        s.record_post({"id": f"b{i}", "title": f"b{i}", "english_title": f"breaking {i}", "url": "u",
                       "kind": "breaking", "posted_at": now.isoformat()})
    assert rank.select_breaking([good], s, now) is None
