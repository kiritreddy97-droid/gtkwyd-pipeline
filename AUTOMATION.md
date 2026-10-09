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

## Instagram storytelling Reels (`story.py`)

Once a day, separately from the video/Short schedule, `story.py` picks a
premise from `topics-stories.txt`, has the AI writer invent an **original**
3-4 minute fictional short story (never a retelling of an existing book/film -
see `pipeline.guardrails`/`pipeline.writer`'s `kind="story"` checks), renders
it as a vertical video via `make_video.py --portrait`, and posts the **whole**
video straight to Instagram (`pipeline.instagram.publish_story`) - this is
Instagram-only content, there's no matching YouTube upload. Standard Reels
cap out around ~90s, so posting tries `media_type=REELS` first and falls back
to plain feed `VIDEO` only when Instagram rejects it specifically for length
(`pipeline.instagram.post_video_or_reel`).

```powershell
.\.venv\Scripts\python.exe story.py                # generate, render, post
.\.venv\Scripts\python.exe story.py --dry-run      # render only
.\.venv\Scripts\python.exe story.py --status       # queue + history
```

Scheduled by `.github/workflows/story.yml` (~5:43pm Mountain, weekdays). Needs
the same `IG_USER_ID`/`IG_ACCESS_TOKEN` secrets as the regular glimpse posts.

## Instagram satisfying / soothing / fitness-wellness Reels (`reels.py`)

Three more separate Instagram-only daily posts, alongside the storytelling
Reel - satisfying, soothing, and fitness/wellness/nutrition each post **once
a day on their own schedule slot** (not a rotation - each category is its own
cron entry in `reels.yml`, calling `reels.py --category <name>`). A render
failure just retries that same category's slot next time. Each category has
its own theme queue (`topics-satisfying.txt` / `topics-soothing.txt` /
`topics-fitness.txt`) and its own AI-writer system prompt
(`pipeline.writer.generate_reel_script`, `kind="reel"` in guardrails) -
narrated like the rest of the channel (not wordless ASMR), ~30-40s, same
portrait render + Instagram posting path as `story.py`
(`pipeline.instagram.publish_reel`). Fitness/wellness content is
guardrail-blocked from diet-plan/weight-loss/supplement claims by design -
movement, recovery, and nutrition *facts* only, never a prescription.

```powershell
.\.venv\Scripts\python.exe reels.py --category satisfying         # one Reel, posted
.\.venv\Scripts\python.exe reels.py --category soothing --dry-run # render only
.\.venv\Scripts\python.exe reels.py --status                      # queues + history
```

Scheduled by `.github/workflows/reels.yml`: satisfying ~12:17pm, soothing
~1:26pm, fitness ~4:47pm Mountain, weekdays.

## Instagram mind-blowing fact-image posts (`fact_post.py`)

A sixth daily Instagram post - a single static feed **image**, not a Reel.
Pulls the hook line from the most recently uploaded YouTube video/Short
(`auto.py` records it into that history.jsonl entry's `"hook"` field),
builds a bold branded "MIND-BLOWING FACT" card (`pipeline/fact_card.py` -
PIL only, no stock footage/rendering pipeline), and posts it via
`pipeline.instagram.post_image`/`publish_fact_image` with a caption + comment
linking the full video. Meant to drive shares/engagement back to YouTube.

```powershell
.\.venv\Scripts\python.exe fact_post.py             # build, post
.\.venv\Scripts\python.exe fact_post.py --dry-run   # build only
```

Scheduled by `.github/workflows/fact-image.yml` (~10:52am Mountain,
weekdays) - much lighter than the others (no ffmpeg/Piper/Whisper/Ollama).

## QA gatekeeper (`pipeline/qa_gate.py`, `pipeline/qa_queue.py`)

> **Currently OFF** (`[qa] enabled = false` in `config.example.toml`): reels,
> stories, images and glimpses post without the AI visual check, with no API
> call. Set it back to `true` (and make sure the Anthropic account has credit)
> to re-enable everything below.

Every Reel/story and fact-image is checked **before** it's staged/posted -
not just for the six daily Instagram posts above, but the glimpse Reels cut
from each full YouTube video too (`pipeline.instagram.stage_glimpse`).
Checks:

- **Reels/stories**: do the sampled frames plausibly relate to the spoken
  narration, and is the cover thumbnail clean (not glitchy/garbled/blank)?
- **Fact-image posts**: does it look like an intentionally designed graphic
  (not broken/glitchy), and is the text legible and on-topic?

A failure regenerates (a fresh script + render for story/reels/fact-image;
a re-cut with a new random highlight for glimpses) up to 3 attempts. If it
still hasn't passed after that: the slot is skipped for today (logged
clearly in history.jsonl as `status: "qa_held"`) and the last attempt is
held in `.qa_held_queue.json` (git-tracked, not gitignored) so the **next**
scheduled run for that category tries it again first, instead of losing it
- glimpses are the one exception, since they're tied to one specific
  already-published video rather than a recurring daily slot, so a
  repeated failure there just skips Instagram for that video.

Needs `ANTHROPIC_API_KEY` (a GitHub Secret, same pattern as the other keys)
to actually do anything - **without it, every check is skipped and logged,
never a hard failure**, so the pipeline keeps working exactly as before
until the key is added. Get a key at console.anthropic.com, then:

```powershell
gh secret set ANTHROPIC_API_KEY --body "sk-ant-..."
```

## Narration voice (`pipeline/tts.py`, `[voice]` in config)

Narration now uses Microsoft's **Edge neural voices** (free, via the `edge-tts`
package; `[voice] engine = "edge"`): a rotating pool of natural-sounding
English voices (Andrew, Brian, Ava, Emma, Ryan, Sonia) with a small per-video
pace/pitch jitter. They also return exact word timings, so captions are built
from the script text itself - more accurate than speech recognition, and no
Whisper model download. If Edge can't be reached for a render, English falls
back to the Piper voices automatically (`engine = "piper"` forces Piper).
Caveat: Edge-TTS uses Microsoft's public read-aloud service without an official
agreement, so treat it as best-effort; the Piper fallback is the safety net.

## Script openers (`pipeline/writer.py`, `pipeline/hookscore.py`)

The writer prompts require a real hook as the first sentence (a question the
viewer can't answer yet, a number from the script, "you" early, or what they'd
miss). If an opener still scores weak on the five-property hook scorer, the
model proposes five rewrites; only one that adds no new number, avoids the
banned/advice patterns, and beats the original by 8+ points is swapped in.

## Telugu history channel (`telugu.py`, `.github/workflows/telugu.yml`)

A second, separate YouTube channel: **every day of the year, from 12:00 PM US
Eastern, one new ~10-minute narrated history story and one new ~60-second
Short**, in Telugu, with matching stock footage, word-timed Telugu captions and
a new thumbnail (a huge yellow 2-4 word curiosity hook that does not repeat the
title). The Short is written together with the day's story.

Scheduling: GitHub cron is UTC-only, so `telugu.yml` has triggers at 16:07,
17:07, 18:37, 20:07 and 23:07 UTC. A cheap `gate` job (`telugu_gate.py`) lets
the first run at/after 12:00 ET do the work (16:07 UTC in summer, 17:07 UTC in
winter) and the rest exit in seconds once today's story and Short are up; the
later ones are backups for when GitHub delays or drops a run.

**YouTube upload quota** is per Google Cloud project (~6 uploads/day at the
default 10,000 units; a video upload costs 1,600). The English channel already
uses most of it, so give the Telugu channel its own project: create a second
project in Google Cloud Console, enable the YouTube Data API v3, create an
OAuth client (Desktop app), download it as `client_secret_te.json` into the
repo folder (git-ignored), and authorise with
`python -m pipeline.youtube --auth --token youtube_token_te.json --client client_secret_te.json`.

- **Writer**: Google Gemini (free tier; the local 3B model can't write Telugu).
  Structured output, strict validation (Telugu script, 640-900 words / 12-20
  scenes for the story), and a second fact-check/language review pass. Topics
  come from `topics-telugu-history.txt`; when fewer than 10 remain, Gemini adds
  40 more. The model name is configurable and auto-replaced if retired.
- **Voice**: `te-IN-ShrutiNeural` / `te-IN-MohanNeural` (Edge).
- **State**: `history-telugu.jsonl` (separate from `history.jsonl`, so the English
  channel's daily cap and health checks never see it).
- **Visibility**: `[telugu] visibility = "unlisted"` until you've watched a few
  episodes, then set `"public"` in `config.example.toml` (CI builds its config
  from that file).

One-time setup (the pipeline no-ops cleanly until both are done):

```powershell
# 1. Free Gemini key from https://aistudio.google.com/apikey
gh secret set GEMINI_API_KEY --body "<your key>"

# 2. Authorise the Telugu channel (log in with the Google account / brand
#    channel it should upload to), then store the token as a secret
.venv\Scripts\python -m pipeline.youtube --auth --token youtube_token_te.json
Get-Content youtube_token_te.json -Raw | gh secret set YT_TE_TOKEN
```

Check things on GitHub's servers any time with **Actions -> telugu -> Run
workflow**: `selftest` renders a bundled sample (no key or login needed;
download the video from the run's artifacts), `gemini-check` lists which Gemini
models your key can actually use (model names get retired and some have no free
quota; the writer probes and picks a working one automatically, and a Gemini
outage leaves the story topic unused). Local status: `python telugu.py --status`.

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
