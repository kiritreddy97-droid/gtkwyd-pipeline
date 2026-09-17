# Full automation (`auto.py`)

One command makes one piece of content and (optionally) uploads it:

```powershell
.\.venv\Scripts\python.exe auto.py --format video               # a long video
.\.venv\Scripts\python.exe auto.py --format short               # a Short
.\.venv\Scripts\python.exe auto.py --format video --source news # a science-news explainer
.\.venv\Scripts\python.exe auto.py --status                     # queue + history
.\.venv\Scripts\python.exe auto.py --format video --dry-run     # render, no upload
```

The Windows scheduler runs it four times each weekday and the channel runs itself.

## Weekday schedule (`schedule-install.ps1`)

| Time (Mon-Fri) | Task | What it makes |
|---|---|---|
| 09:00 | `GTKWYD-video-am` | Long video - tries a **science/space/AI news** explainer first, falls back to the bank |
| 10:00 | `GTKWYD-short-am` | Short |
| 15:00 | `GTKWYD-video-pm` | Long video from the bank |
| 15:30 | `GTKWYD-short-pm` | Short |

```powershell
.\schedule-install.ps1            # install
.\schedule-install.ps1 -Remove    # delete all GTKWYD tasks
```

Edit `$SLOTS` at the top of the script to change times. The PC must be on and
signed in; a missed run fires when the PC is next available.

## Where content comes from

**Long videos** (`--format video`):
1. `scripts/ready/` - anything you approved by hand
2. **News** (only the 09:00 slot) - a fresh headline from curated science/space/
   tech feeds, explained. See `pipeline/news.py` for the feed list. Nothing
   political, tragic, or breaking gets through the filter.
3. `scripts/bank/` - 55 hand-written, verified scripts (facts, "what if...",
   "what happens when...")
4. AI writer from `topics.txt` - **goes to `scripts/review/`**, not published

**Shorts** (`--format short`):
1. `scripts/shorts-ready/`
2. `scripts/shorts-bank/` - 21 hand-written Shorts scripts
3. AI writer from `topics-shorts.txt` - **goes to `scripts/shorts-review/`**

Each script is used once, then moved to `scripts/published/`. `history.jsonl`
logs every run; `auto.log` is the readable log.

## The AI review gate

`[auto] require_review_for_ai = true` (default). AI-written scripts - including
news explainers - land in a review folder and are **not** uploaded. You skim
them and move the good ones into `scripts/ready/` or `scripts/shorts-ready/`.

Why it stays on: the local model is small and gets facts wrong often enough that
unreviewed publishing would eventually put a wrong claim on a channel whose whole
promise is "learn something true". The 55 video + 21 Short bank scripts give you
~4 weeks of hands-off runway before AI content is even needed.

Set it to `false` only once you have watched enough AI drafts to trust them.

## Config (`config.toml` -> `[auto]`)

| Key | Meaning |
|---|---|
| `visibility` | `private` / `unlisted` / `public` |
| `require_review_for_ai` | AI + news scripts wait for your approval (keep `true`) |
| `self_review` | model fact-checks its own draft before it reaches the queue |
| `max_per_day` | hard upload ceiling (default 6; the schedule only does 4) |
| `ollama_model` | `llama3.2:3b` (fast) or `llama3.1:8b` (slower, more accurate) |
| `playlist_id` | optional; every upload is added to it |

## Music

Every render adds a quiet random track from `assets/music/`. See
**MUSIC_SETUP.md** - use the YouTube Audio Library only.

## Instagram cross-posting

Every full video (not Shorts) gets a ~20s glimpse clip auto-posted to
Instagram Reels ~1h after it goes live on YouTube. See **INSTAGRAM_SETUP.md**
for the one-time setup - until it's done, glimpses stage silently but nothing
posts. Runs across two files: `auto.py` stages the clip right after upload
(`pipeline/instagram.py`), and `.github/workflows/instagram.yml` posts
whatever's due on a schedule.

## Monitoring

```powershell
.\.venv\Scripts\python.exe auto.py --status
Get-Content auto.log -Tail 30
```

## Before going fully hands-off public

- **Verify your phone** at youtube.com/verify (needed for public + thumbnails).
- Start at `visibility = "unlisted"`, watch a week of output, then switch to
  `"public"`.
- Do **not** run `auto.py` by hand while the scheduler is active (no `--dry-run`)
  - you would double-post. `max_per_day` is the backstop.
- 4 posts/weekday on a new channel is already on the high side for YouTube's spam
  systems. If you see reduced reach or a warning, drop to 2/day (comment out
  slots in `schedule-install.ps1`).
