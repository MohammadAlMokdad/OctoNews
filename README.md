# OctoNews 🐙

An automated WhatsApp Channel that posts **technology, cybersecurity and AI news**, with extra weight on stories about **Lebanon**. Each post has an Arabic summary followed by an English one. Everything runs on free tiers:

| Part | Service | Free limit | This bot uses |
|---|---|---|---|
| Scheduler | GitHub Actions (hourly) | 2,000 min/month (private repo) | ~1,100 min/month |
| AI screening + writing | Gemini 3.5 Flash Lite (free tier; 3.1 Flash Lite as backup) | 15/min, 500/day | ~35–40 requests/day |
| WhatsApp sending | Whapi.Cloud Sandbox | 150 msgs/day, 1,000 requests/month | ≤11 posts/day, ~330/month |

> ⚠️ Meta has **no official API** for WhatsApp Channels. Whapi is an unofficial gateway linked to a WhatsApp account by QR code. **Use a dedicated phone number, never your personal one.**

## How it works

Every hour, the GitHub Action runs once:

1. **Fetch.** Reads the RSS feeds in [`sources.yaml`](sources.yaml): global cyber, tech and AI outlets, MENA tech sites, Lebanese outlets, and Google News searches about Lebanon in English, Arabic and French.
2. **Screen.** Sends new items to Gemini in batches. Gemini classifies each item (cybersecurity / AI / tech / off-topic), rates its importance (1–10) and its Lebanon relevance (0–3), and flags breaking news and duplicates. Duplicates include the same story already posted by another outlet.
3. **Queue.** Keeps worthwhile items for up to 24 hours. Lebanon stories get a ranking boost.
4. **Post.**
   - **Slots:** at **08:00, 13:00 and 19:00 Beirut time**, the bot posts up to 3 of the best stories, with a maximum of 9 a day. The evening slot guarantees at least one Lebanon/MENA story per day when one exists.
   - **Breaking:** an urgent, fresh, important Lebanon story posts right away, up to 2 a day and never between 23:00 and 07:00.
   - For each story, Gemini writes the Arabic and English text from the full article. The code adds the emoji, link and hashtags, so links can't be made up.
5. **Remember.** Saves `state.json` (seen items, queue, post log) to a separate `bot-state` branch.

Example post:

```
🇱🇧 🛡️ *اختراق موقع وزارة ...*
ملخص من جملتين أو ثلاث ...

*Ministry website hacked ...*
Two to three sentence summary ...

🔗 https://publisher.com/article
#Cybersecurity #Lebanon #لبنان #DataBreach
```

## One-time setup (about 30 minutes)

1. **A dedicated WhatsApp number.** A prepaid alfa/touch SIM in a spare phone works. Install WhatsApp on it.
   - **That phone must come online at least every ~14 days**, or WhatsApp unlinks Whapi.
2. **Create the channels** in the WhatsApp app on that number (Updates → Channels → Create): your real channel, plus a second private one such as "OctoNews Test" for testing.
3. **Whapi.Cloud.** Sign up at <https://whapi.cloud> (Sandbox plan, no credit card) and scan the QR code from WhatsApp → *Linked devices*. Copy the API token.
4. **Gemini.** Create a free API key at <https://aistudio.google.com/apikey>.
5. **Local test** (Windows PowerShell):
   ```powershell
   python -m venv .venv
   .\.venv\Scripts\pip install -r requirements.txt
   copy .env.example .env      # then fill in GEMINI_API_KEY and WHAPI_TOKEN
   .\.venv\Scripts\python -m octonews.main --list-channels   # copy the TEST channel id into .env as WHAPI_CHANNEL_ID
   .\.venv\Scripts\python -m octonews.main --send-test       # a test message should appear in the test channel
   .\.venv\Scripts\python -m octonews.main --dry-run --force-slot   # real AI, posts printed, nothing sent
   ```
6. **GitHub.** Create a **private** repository and push this folder. Then go to *Settings → Secrets and variables → Actions* and add these secrets:
   - `GEMINI_API_KEY`
   - `WHAPI_TOKEN`
   - `WHAPI_CHANNEL_ID`: the **test** channel ID to start with.
   - Optional: *variables* named `GEMINI_SCREEN_MODELS` and `GEMINI_WRITE_MODELS` to change the model chains (see `.env.example`).
7. **First runs.** Go to *Actions → OctoNews → Run workflow*:
   - First with *dry run* ticked: check the log output.
   - Then with *dry run* unticked and *force slot* ticked: a real post should land in the test channel.
   - Leave it running on the test channel for a day.
8. **Go live.** Change the `WHAPI_CHANNEL_ID` secret to the real channel's ID and share its invite link.

## Commands

| Command | What it does |
|---|---|
| `python -m octonews.main` | Normal run (what the hourly Action does) |
| `--dry-run` | Print posts instead of sending them; state is not saved |
| `--force-slot` | Act as if a posting slot is due right now |
| `--check-feeds` | Test every feed in `sources.yaml` |
| `--list-models` | List the Gemini models your key can use |
| `--list-channels` | Show your WhatsApp channels and their IDs (uses 1 Whapi request) |
| `--send-test` | Post a short test message to `WHAPI_CHANNEL_ID` |
| `pytest` | Run the tests (no keys or network needed) |

## Tuning

- **Sources:** edit [`sources.yaml`](sources.yaml), then run `--check-feeds`. For busy general-news feeds, set `keywords: true` so only tech-related items reach the AI.
- **Schedule, caps and ranking:** the constants at the top of [`octonews/config.py`](octonews/config.py): `SLOT_HOURS`, `MAX_POSTS_PER_SLOT`, `LEBANON_BOOST`, `MIN_SCORE_TO_POST`, and so on.
- **Editorial voice and rules:** [`prompts/screen.md`](prompts/screen.md) and [`prompts/write.md`](prompts/write.md).
- **Better Arabic writing:** only [`octonews/llm.py`](octonews/llm.py) talks to the AI. Swapping in Claude (Haiku 5.5 costs about $1–2/month) means changing that one file.

## Notes and limits

- Some sites block automated readers. The bot identifies itself honestly and drops any feed that refuses it, instead of disguising itself. CISA, AITNews, MENAbytes and L'Orient Today were removed for this reason.
- If a run fails (for example, Whapi is down), GitHub emails you. The next hourly run retries automatically, and nothing is posted twice.
- Posts contain only a short summary and a link, never the full article.
- Gemini's free tier may use your inputs to improve Google's products. The inputs here are public news, so that is acceptable.
