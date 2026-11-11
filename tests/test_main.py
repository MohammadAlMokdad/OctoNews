"""End-to-end run with fake feeds, a fake AI editor and a fake WhatsApp sender (no network, no keys)."""

from datetime import timedelta

import pytest
from conftest import NOW

from octonews import extract, main
from octonews.config import Settings
from octonews.feeds import Article
from octonews.formatter import PostContent
from octonews.llm import ScreenItem


def article(n, title, hours_old=1, region="global"):
    return Article(id=f"a{n}", title=title, url=f"https://ex.com/{n}", source="Src", region=region, lang="en",
                   published=(NOW - timedelta(hours=hours_old)).isoformat(), snippet="")


ARTICLES = [
    article(1, "Ransomware gang hits European hospitals"),
    article(2, "Lebanese ministry website hacked, group claims responsibility", region="lebanon"),
    article(3, "Celebrity wedding photos"),
    article(4, "New open-weight AI model tops benchmarks"),
    article(5, "Old story from two days ago", hours_old=48),
    article(6, "Covered elsewhere: same ransomware story, other outlet"),
]


class FakeEditor:
    def __init__(self):
        self.screened, self.written = 0, 0

    def screen_batch(self, batch, known_titles=None):
        self.screened += len(batch)
        self.known_titles = known_titles
        out = {}
        for i, a in enumerate(batch):
            leb = 3 if "Lebanese" in a.title else 0
            cat = "other" if "Celebrity" in a.title else ("ai" if "AI" in a.title else "cybersecurity")
            out[i] = ScreenItem(idx=i, english_title=a.title, category=cat, importance=8,
                                lebanon_relevance=leb, breaking=bool(leb), duplicate_of=-1,
                                already_covered="Covered" in a.title)
        return out

    def filter_duplicates(self, pool, recent_titles):
        self.dedupe_calls = getattr(self, "dedupe_calls", 0) + 1
        self.recent_titles = recent_titles
        return {c.id for c in pool if "Repeat" not in c.title}

    def write_post(self, a, text):
        self.written += 1
        return PostContent("عنوان عربي", "ملخص عربي قصير.", a.title, "Short English summary.", ["Test"]), ""


class FakeSender:
    def __init__(self, fail=False):
        self.sent, self.fail = [], fail

    def send_text(self, channel, body):
        if self.fail:
            raise RuntimeError("gateway down")
        self.sent.append(body)
        return f"m{len(self.sent)}"


@pytest.fixture
def settings(tmp_path):
    return Settings(feeds=[], channel_id="123@newsletter", state_file=tmp_path / "state.json")


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    monkeypatch.setattr(extract, "resolve_url", lambda u: u)
    monkeypatch.setattr(extract, "fetch_article_text", lambda u: "article text")
    monkeypatch.setattr(extract, "shorten_url", lambda u, headline="": u)
    monkeypatch.setattr(main.time, "sleep", lambda s: None)


def fetch(feeds, now):
    return list(ARTICLES), {}


def test_full_run_posts_breaking_then_slot_and_is_idempotent(settings):
    editor, sender = FakeEditor(), FakeSender()
    code = main.run(settings, now=NOW, editor_factory=lambda: editor, sender=sender, fetch=fetch)
    assert code == 0
    assert editor.screened == 5  # the 48h-old item is never sent to the AI
    assert len(sender.sent) == 3  # 1 breaking (Lebanon) + 2 slot posts
    assert sender.sent[0].startswith("🚨")
    assert "🇱🇧" in sender.sent[0]
    assert not any("Celebrity" in m or "Covered" in m for m in sender.sent)

    # Same hour again: nothing new to screen, slot already done -> no posts.
    sender2 = FakeSender()
    main.run(settings, now=NOW + timedelta(minutes=30), editor_factory=lambda: editor, sender=sender2, fetch=fetch)
    assert sender2.sent == []
    assert editor.screened == 5


def test_dry_run_does_not_save_state(settings, capsys):
    main.run(settings, dry_run=True, now=NOW, editor_factory=FakeEditor, fetch=fetch)
    assert not settings.state_file.exists()
    assert "Ransomware gang hits European hospitals" in capsys.readouterr().out


def test_send_failure_keeps_slot_open_for_retry(settings):
    editor = FakeEditor()
    code = main.run(settings, now=NOW, editor_factory=lambda: editor, sender=FakeSender(fail=True), fetch=fetch)
    assert code == 1
    sender = FakeSender()
    main.run(settings, now=NOW + timedelta(hours=1), editor_factory=lambda: editor, sender=sender, fetch=fetch)
    assert len(sender.sent) == 3  # retried successfully an hour later


def test_ai_outage_mid_slot_is_not_a_failure_and_slot_resumes_without_overfilling(settings):
    extra = [article(10 + i, f"Distinct cyber story about topic {name}") for i, name in
             enumerate(["routers", "printers", "satellites", "elections"])]

    def fetch_more(feeds, now):
        return [a for a in ARTICLES if "Lebanese" not in a.title] + extra, {}  # no breaking item

    class FlakyEditor(FakeEditor):
        def write_post(self, a, text):
            if self.written >= 1:
                raise RuntimeError("503 high demand")
            return super().write_post(a, text)

    editor, sender = FlakyEditor(), FakeSender()
    code = main.run(settings, now=NOW, editor_factory=lambda: editor, sender=sender, fetch=fetch_more)
    assert code == 0  # AI outage: warning only, no failure email
    assert len(sender.sent) == 1

    sender2 = FakeSender()
    later = NOW + timedelta(hours=1)
    main.run(settings, now=later, editor_factory=FakeEditor, sender=sender2, fetch=fetch_more)
    assert len(sender2.sent) == 2  # completes the 08:00 slot: 1 + 2 = MAX_POSTS_PER_SLOT

    sender3 = FakeSender()
    main.run(settings, now=later + timedelta(hours=1), editor_factory=FakeEditor, sender=sender3, fetch=fetch_more)
    assert sender3.sent == []  # slot now done


def test_paused_until_blocks_posting_then_resumes_missed_slot(settings):
    from dataclasses import replace

    paused = replace(settings, paused_until=NOW + timedelta(hours=2))
    sender = FakeSender()
    main.run(paused, now=NOW, editor_factory=FakeEditor, sender=sender, fetch=fetch)
    assert sender.sent == []  # 08:07 slot due, but paused (no breaking either)

    sender2 = FakeSender()
    main.run(paused, now=NOW + timedelta(hours=2, minutes=1), editor_factory=FakeEditor, sender=sender2, fetch=fetch)
    assert len(sender2.sent) == 3  # 10:08 Beirut: pause over, the 08:00 slot (+ breaking) goes out


def test_force_slot_overrides_pause(settings):
    from dataclasses import replace

    sender = FakeSender()
    main.run(replace(settings, paused_until=NOW + timedelta(days=1)), force_slot=True, now=NOW,
             editor_factory=FakeEditor, sender=sender, fetch=fetch)
    assert len(sender.sent) >= 1


def test_ai_duplicate_check_drops_repeats_before_posting(settings):
    def fetch_with_repeat(feeds, now):
        return [a for a in ARTICLES if "Lebanese" not in a.title] + [
            article(20, "Repeat: hospitals hit by ransomware gang, says report")], {}

    editor, sender = FakeEditor(), FakeSender()
    main.run(settings, now=NOW, editor_factory=lambda: editor, sender=sender, fetch=fetch_with_repeat)
    assert editor.dedupe_calls == 1
    assert not any("Repeat" in m for m in sender.sent)
    assert len(sender.sent) == 2  # ransomware + AI model; the repeat was dropped, not posted
    state = main.State.load(settings.state_file)
    assert "a20" not in state.candidates  # removed from the queue for good


def test_duplicate_check_failure_falls_back_to_title_matching(settings):
    class Broken(FakeEditor):
        def filter_duplicates(self, pool, recent_titles):
            raise RuntimeError("503 high demand")

    sender = FakeSender()
    code = main.run(settings, now=NOW, editor_factory=Broken, sender=sender, fetch=fetch)
    assert code == 0 and len(sender.sent) == 3


def test_no_posts_during_quiet_hours(settings):
    sender = FakeSender()
    early = NOW.replace(hour=4)  # 06:07 Beirut: before the first slot and inside quiet hours
    main.run(settings, now=early, editor_factory=FakeEditor, sender=sender, fetch=fetch)
    assert sender.sent == []


def test_known_titles_are_passed_to_screening(settings):
    editor = FakeEditor()
    s = main.State()
    s.record_post({"id": "p", "title": "t", "english_title": "Earlier posted story", "url": "u",
                   "kind": "slot", "posted_at": NOW.isoformat()})
    s.save(settings.state_file)
    main.run(settings, now=NOW, editor_factory=lambda: editor, sender=FakeSender(), fetch=fetch)
    assert "Earlier posted story" in editor.known_titles
