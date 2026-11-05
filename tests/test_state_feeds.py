from datetime import timedelta

from conftest import make_candidate

from octonews import config
from octonews.config import Feed
from octonews.feeds import article_id, matches_tech_keywords, normalize_url, parse_feed
from octonews.state import State


def test_state_round_trip_and_prune(tmp_path, now):
    path = tmp_path / "state.json"
    s = State()
    s.mark_seen(["a", "b"], now)
    s.mark_seen(["old"], now - timedelta(days=config.SEEN_RETENTION_DAYS + 1))
    c = make_candidate(1)
    s.add_candidate(c.id, c.to_dict())
    stale = make_candidate(2, hours_old=config.MAX_ARTICLE_AGE_HOURS + 1)
    s.add_candidate(stale.id, stale.to_dict())
    s.mark_slot_done(now.astimezone(config.TZ).date(), 8)
    s.save(path)

    loaded = State.load(path)
    loaded.prune(now)
    assert loaded.is_seen("a") and not loaded.is_seen("old")
    assert list(loaded.candidates) == ["id1"]
    assert loaded.is_slot_done(now.astimezone(config.TZ).date(), 8)


def test_record_post_removes_candidate_and_counts(now):
    s = State()
    c = make_candidate(1, lebanon=2)
    s.add_candidate(c.id, c.to_dict())
    s.record_post({"id": c.id, "title": c.title, "english_title": c.title, "url": c.url, "kind": "slot",
                   "lebanon_relevance": 2, "posted_at": now.isoformat()})
    day = now.astimezone(config.TZ).date()
    assert c.id not in s.candidates
    assert s.count_posts_on(day, "slot") == 1
    assert s.count_posts_on(day, "breaking") == 0
    assert s.posted_lebanon_on(day)


def test_load_missing_file(tmp_path):
    assert State.load(tmp_path / "nope.json").candidates == {}


def test_normalize_url_strips_tracking():
    a = normalize_url("https://Example.com/news/story/?utm_source=x&id=5#top")
    assert a == "https://example.com/news/story?id=5"
    assert article_id("https://example.com/news/story?id=5&utm_medium=rss") == article_id(a)


def test_keyword_filter():
    assert matches_tech_keywords("Ogero announces internet outage in Beirut")
    assert matches_tech_keywords("اختراق موقع وزارة الشؤون الاجتماعية")
    assert matches_tech_keywords("Le Liban adopte une stratégie d'intelligence artificielle")
    assert matches_tech_keywords("New AI rules")
    assert not matches_tech_keywords("Minister said the meeting was productive")
    assert not matches_tech_keywords("أمطار غزيرة تضرب لبنان")


RSS = b"""<?xml version="1.0"?><rss version="2.0"><channel><title>T</title>
<item><title>Hackers breach ministry site</title><link>https://ex.com/1?utm_source=rss</link>
<description>&lt;p&gt;Group claims &lt;b&gt;attack&lt;/b&gt;&lt;/p&gt;</description>
<pubDate>Thu, 05 Nov 2026 05:00:00 GMT</pubDate></item>
<item><title>Football results</title><link>https://ex.com/2</link><description>Scores</description></item>
<item><title>No link item</title></item>
</channel></rss>"""


def test_parse_feed_with_keyword_filter(now):
    feed = Feed(name="Test", url="u", region="lebanon", lang="en", keywords=True)
    items = parse_feed(feed, RSS, now)
    assert [a.title for a in items] == ["Hackers breach ministry site"]
    a = items[0]
    assert a.snippet == "Group claims attack"
    assert a.published.startswith("2026-11-05T05:00")
    assert a.region == "lebanon"

    unfiltered = parse_feed(Feed(name="Test", url="u"), RSS, now)
    assert len(unfiltered) == 2
    assert unfiltered[1].published == now.isoformat()  # missing date falls back to now
