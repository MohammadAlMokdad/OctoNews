"""Model fallback logic, with the Gemini client replaced by a fake (no network)."""

from types import SimpleNamespace

import pytest
from google.genai import errors

from octonews import llm
from octonews.feeds import Article


class FakeResponse:
    def __init__(self, parsed):
        self.parsed, self.text = parsed, None


class FakeModels:
    def __init__(self, behaviour):
        self.behaviour, self.calls = behaviour, []

    def generate_content(self, model, contents, config):
        self.calls.append(model)
        outcome = self.behaviour.get(model, "ok")
        if isinstance(outcome, int):
            raise errors.APIError(outcome, {"error": {"code": outcome, "message": "x", "status": "X"}})
        return FakeResponse(llm.ScreenResult(items=[llm.ScreenItem(
            idx=0, english_title="T", category="ai", importance=14, lebanon_relevance=-2,
            breaking=False, duplicate_of=-1, already_covered=False)]))


def editor(monkeypatch, behaviour, models="busy,good,spare"):
    monkeypatch.setattr(llm.time, "sleep", lambda s: None)
    ed = llm.Editor("dummy-key", models, "writer")
    ed.client = SimpleNamespace(models=FakeModels(behaviour))
    return ed


def test_screening_and_writing_use_separate_chains(monkeypatch):
    ed = editor(monkeypatch, {})
    calls = []
    monkeypatch.setattr(ed, "_generate", lambda models, *a: calls.append(models) or (_ for _ in ()).throw(StopIteration))
    for fn in (lambda: ed.screen_batch(ART), lambda: ed.write_post(ART[0], "")):
        try:
            fn()
        except StopIteration:
            pass
    assert calls == [["busy", "good", "spare"], ["writer"]]


ART = [Article(id="a", title="t", url="u", source="s", region="global", lang="en",
               published="2026-11-05T06:00:00+00:00", snippet="")]


def test_busy_model_falls_back_but_is_retried_on_next_request(monkeypatch):
    ed = editor(monkeypatch, {"busy": 503})
    out = ed.screen_batch(ART)
    assert out[0].importance == 10 and out[0].lebanon_relevance == 0  # clamped to valid ranges
    assert ed.client.models.calls == ["busy", "busy", "good"]
    ed.screen_batch(ART)  # 503 is momentary: the preferred model gets another chance
    assert ed.client.models.calls[3:] == ["busy", "busy", "good"]


@pytest.mark.parametrize("code", [404, 429])
def test_retired_or_out_of_quota_model_is_skipped_for_the_run(monkeypatch, code):
    ed = editor(monkeypatch, {"busy": code})
    ed.screen_batch(ART)
    ed.screen_batch(ART)
    assert ed.client.models.calls == ["busy", "good", "good"]


def test_second_round_over_the_chain(monkeypatch):
    class Flaky(FakeModels):
        def generate_content(self, model, contents, config):
            if len(self.calls) < 6:  # first full pass over 3 models x 2 attempts fails
                self.calls.append(model)
                raise errors.APIError(503, {"error": {"code": 503, "message": "x", "status": "X"}})
            return super().generate_content(model, contents, config)

    ed = editor(monkeypatch, {})
    ed.client = SimpleNamespace(models=Flaky({}))
    ed.screen_batch(ART)
    assert ed.client.models.calls == ["busy", "busy", "good", "good", "spare", "spare", "busy"]


def test_non_retryable_error_is_raised(monkeypatch):
    ed = editor(monkeypatch, {"busy": 400})
    with pytest.raises(errors.APIError):
        ed.screen_batch(ART)


def test_all_models_down_raises_after_all_rounds(monkeypatch):
    ed = editor(monkeypatch, {"busy": 503, "good": 503, "spare": 429})
    with pytest.raises(errors.APIError):
        ed.screen_batch(ART)
    calls = ed.client.models.calls
    assert calls.count("spare") == 1  # 429: skipped after the first try
    assert calls.count("busy") == calls.count("good") == 2 * llm.CHAIN_ROUNDS
