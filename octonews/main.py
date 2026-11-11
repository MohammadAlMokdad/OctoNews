"""One OctoNews run: fetch -> screen -> (maybe) write & post -> save state.

Usage:
  python -m octonews.main                 # normal run (what the scheduler runs every hour)
  python -m octonews.main --dry-run       # print posts instead of sending; state is not saved
  python -m octonews.main --dry-run --force-slot   # pretend a posting slot is due right now
  python -m octonews.main --check-feeds   # test every feed in sources.yaml
  python -m octonews.main --list-models   # Gemini models available to your key
  python -m octonews.main --list-channels # your WhatsApp channels and their ids
  python -m octonews.main --send-test     # post a short test message to WHATSAPP_CHANNEL_ID
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from datetime import datetime, timedelta, timezone

from . import config, extract, feeds, formatter, rank
from .config import Settings
from .rank import Candidate
from .state import State, slot_key_for

log = logging.getLogger("octonews")

SECONDS_BETWEEN_POSTS = 8


def _screen_new_articles(articles, state: State, editor_factory, now: datetime) -> int:
    """Screen unseen, fresh articles and add the keepers to the candidate queue. Returns # kept."""
    max_age = timedelta(hours=config.MAX_ARTICLE_AGE_HOURS)
    unseen = [a for a in articles if not state.is_seen(a.id)]
    too_old = [a for a in unseen if now - a.published_dt > max_age]
    state.mark_seen((a.id for a in too_old), now)

    fresh = sorted((a for a in unseen if now - a.published_dt <= max_age), key=lambda a: a.published, reverse=True)
    to_screen = fresh[: config.MAX_SCREEN_ITEMS_PER_RUN]
    log.info("Articles: %d fetched, %d new & fresh, screening %d", len(articles), len(fresh), len(to_screen))
    if not to_screen:
        return 0

    editor = editor_factory()
    kept = 0
    for start in range(0, len(to_screen), config.SCREEN_BATCH_SIZE):
        batch = to_screen[start : start + config.SCREEN_BATCH_SIZE]
        try:
            results = editor.screen_batch(batch, rank.known_titles(state, now))
        except Exception as exc:  # leave the rest unseen; the next hourly run retries them
            log.error("Screening failed, will retry next run: %s", exc)
            break
        kept_in_batch: set[int] = set()
        for i, article in enumerate(batch):
            state.mark_seen([article.id], now)
            item = results.get(i)
            if item is None or item.category == "other" or item.importance < config.MIN_IMPORTANCE_TO_KEEP:
                continue
            if item.already_covered or item.duplicate_of in kept_in_batch:
                continue
            kept_in_batch.add(i)
            cand = Candidate(
                **article.to_dict(),
                english_title=item.english_title,
                category=item.category,
                importance=item.importance,
                lebanon_relevance=item.lebanon_relevance,
                breaking=item.breaking,
                screened_at=now.isoformat(),
            )
            state.add_candidate(article.id, cand.to_dict())
            kept += 1
    log.info("Kept %d new candidates (queue size now %d)", kept, len(state.candidates))
    return kept


def _drop_repeats(candidates: list[Candidate], state: State, now: datetime, editor_factory) -> list[Candidate]:
    pool = rank.shortlist(candidates, now)
    if not pool:
        return candidates
    try:
        keep = editor_factory().filter_duplicates(pool, rank.recent_post_titles(state, now))
    except Exception as exc:  # the title-similarity check in rank.py still applies
        log.warning("AI duplicate check unavailable this run: %s", exc)
        return candidates
    dropped = [c for c in pool if c.id not in keep]
    for c in dropped:
        log.info("Dropped repeat story: %s", c.english_title or c.title)
        state.remove_candidate(c.id)
    dropped_ids = {c.id for c in dropped}
    return [c for c in candidates if c.id not in dropped_ids]


class AIUnavailable(RuntimeError):
    """Gemini couldn't write a post (busy / out of quota). Not an alert-worthy failure: retried next run."""


def _write_and_post(
    c: Candidate, kind: str, editor, sender, settings: Settings, state: State, now, dry_run, slot_key: str = ""
) -> bool:
    """Returns True if posted (or printed in dry-run)."""
    url = extract.resolve_url(c.url)
    text = extract.fetch_article_text(url)
    try:
        content, reason = editor.write_post(c, text)
    except Exception as exc:
        raise AIUnavailable(str(exc)) from exc
    if content is None:
        log.info("Skipped by editor: %s (%s)", c.title, reason)
        state.remove_candidate(c.id)
        return False
    problems = formatter.validate(content)
    if problems:
        log.warning("Rejected post for %r: %s", c.title, "; ".join(problems))
        state.remove_candidate(c.id)
        return False

    link = extract.shorten_url(url, headline=c.title)  # drop Arabic slugs when the short link still works
    message = formatter.build_message(c, content, link, breaking=(kind == "breaking"))
    if dry_run:
        print("\n" + "=" * 60 + f"\n[{kind}] score={rank.score(c, now):.1f} source={c.source}\n" + "-" * 60)
        print(message)
        print("=" * 60)
        message_id = "dry-run"
    else:
        message_id = sender.send_text(settings.channel_id, message)
        log.info("Posted (%s): %s", kind, c.english_title or c.title)

    state.record_post(
        {
            "id": c.id,
            "title": c.title,
            "english_title": c.english_title,
            "url": url,
            "kind": kind,
            "category": c.category,
            "lebanon_relevance": c.lebanon_relevance,
            "posted_at": now.isoformat(),
            "message_id": message_id,
            "slot": slot_key,
        }
    )
    return True


def run(
    settings: Settings,
    *,
    dry_run: bool = False,
    force_slot: bool = False,
    now: datetime | None = None,
    editor_factory=None,
    sender=None,
    fetch=feeds.fetch_all,
) -> int:
    now = now or datetime.now(timezone.utc)
    local_now = now.astimezone(config.TZ)
    log.info("Run at %s Beirut (dry_run=%s, force_slot=%s)", local_now.strftime("%Y-%m-%d %H:%M"), dry_run, force_slot)

    state = State.load(settings.state_file)
    state.prune(now)

    _editor = None

    def get_editor():
        nonlocal _editor
        if _editor is None:
            if editor_factory:
                _editor = editor_factory()
            else:
                from .llm import Editor

                _editor = Editor(settings.gemini_api_key, settings.gemini_screen_models, settings.gemini_write_models)
        return _editor

    exit_code = 0
    try:
        articles, _ = fetch(settings.feeds, now)
        _screen_new_articles(articles, state, get_editor, now)

        candidates = [Candidate.from_dict(d) for d in state.candidates.values()]
        plan: list[tuple[Candidate, str]] = []

        paused = settings.paused_until is not None and now < settings.paused_until and not force_slot
        if paused:
            log.info("Posting paused until %s Beirut (screening only)",
                     settings.paused_until.astimezone(config.TZ).strftime("%Y-%m-%d %H:%M"))

        if force_slot:
            slot = local_now.hour
        else:
            slot = None if paused else rank.due_slot(local_now, state)

        # Before posting anything, let the AI drop stories that repeat a recent post or each other
        # (same event, different outlet or wording), which title matching alone misses.
        if slot is not None or (not paused and rank.select_breaking(candidates, state, now)):
            candidates = _drop_repeats(candidates, state, now, get_editor)

        breaking = None if paused else rank.select_breaking(candidates, state, now)
        if breaking:
            plan.append((breaking, "breaking"))
        incomplete = False
        state_slot_key = slot_key_for(local_now.date(), slot) if slot is not None else ""
        if slot is not None:
            picks = rank.select_for_slot([c for c in candidates if c is not breaking], state, now, slot)
            log.info("Slot %02d:00 due: %d stories selected from %d candidates", slot, len(picks), len(candidates))
            plan += [(c, "slot") for c in picks]
        else:
            log.info("No slot due")

        if plan:
            if not dry_run and sender is None:
                from .whatsapp import make_sender

                if not settings.channel_id:
                    raise RuntimeError("WHATSAPP_CHANNEL_ID is not set")
                sender = make_sender(settings)
            for n, (c, kind) in enumerate(plan):
                if n and not dry_run:
                    time.sleep(SECONDS_BETWEEN_POSTS)
                slot_key = state_slot_key if kind == "slot" else ""
                try:
                    _write_and_post(c, kind, get_editor(), sender, settings, state, now, dry_run, slot_key)
                except AIUnavailable as exc:
                    log.warning("AI unavailable, the rest of this slot will be retried next run: %s", exc)
                    incomplete = True
                    break
                except Exception as exc:
                    log.error("Failed to publish %r: %s", c.title, exc)
                    exit_code = 1  # WhatsApp/gateway problem: fail the run so GitHub emails an alert
                    incomplete = True
                    break
                if not dry_run:
                    state.save(settings.state_file)  # save after every post: never double-post

        if slot is not None and not force_slot and not incomplete:
            state.mark_slot_done(local_now.date(), slot)  # if incomplete, the next hourly run continues it
    except Exception:
        log.exception("Run failed")
        exit_code = 1
    finally:
        if not dry_run:
            state.save(settings.state_file)
    return exit_code


# ---------------------------------------------------------------- CLI helpers


def check_feeds(settings: Settings) -> int:
    _, results = feeds.fetch_all(settings.feeds)
    bad = 0
    for name, (count, error) in results.items():
        status = "FAIL" if error else "ok  "
        bad += bool(error)
        print(f"{status} {count:3d} items  {name}" + (f"  -> {error[:120]}" if error else ""))
    print(f"\n{len(results) - bad}/{len(results)} feeds working. (Keyword-filtered feeds can show 0 items.)")
    return 1 if bad else 0


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):  # Arabic + emoji on Windows consoles
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
    for noisy in ("httpx", "google_genai", "trafilatura"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    p = argparse.ArgumentParser(prog="octonews", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--dry-run", action="store_true", help="print posts instead of sending; don't save state")
    p.add_argument("--force-slot", action="store_true", help="treat a posting slot as due now")
    p.add_argument("--check-feeds", action="store_true")
    p.add_argument("--list-models", action="store_true")
    p.add_argument("--list-channels", action="store_true")
    p.add_argument("--send-test", action="store_true")
    args = p.parse_args(argv)

    settings = config.load_settings()

    if args.check_feeds:
        return check_feeds(settings)
    if args.list_models:
        from .llm import list_models

        print("\n".join(list_models(settings.gemini_api_key)))
        return 0
    if args.list_channels or args.send_test:
        from .whatsapp import make_sender

        wa = make_sender(settings)
        if args.list_channels:
            for ch in wa.list_channels():
                print(f"{ch.get('id')}  {ch.get('name', '')}  role={ch.get('role', '?')}")
        else:
            mid = wa.send_text(settings.channel_id, "✅ OctoNews test message / رسالة تجريبية")
            print(f"Sent test message, id={mid}")
        return 0

    return run(settings, dry_run=args.dry_run, force_slot=args.force_slot)


if __name__ == "__main__":
    sys.exit(main())
