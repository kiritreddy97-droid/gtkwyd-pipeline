# Get To Know What You Don't — Faceless Video Pipeline

A near-free, mostly-local pipeline that turns a written script into a finished
YouTube video or Short: narration, stock visuals, burned-in captions, background
music, thumbnail, and metadata — and can publish it on a schedule.

**Channel:** science & space, AI & future tech, hidden history, psychology,
science-news explained, and mind-blowing facts. Family-friendly.
*Question everything. Discover reality.*

- **Manual:** `run.ps1 scripts/<name>.md` makes one video; you upload it.
- **Automatic:** `auto.py` + `schedule-install.ps1` makes and uploads 2 videos +
  2 Shorts every weekday. See **AUTOMATION.md**.

Content: 55 hand-written video scripts + 21 Short scripts in `scripts/bank/` and
`scripts/shorts-bank/`, then a local AI writer (review-gated) and a daily
science-news feed.

---

## What you need (one time)

| Tool | Why | Install |
|---|---|---|
| Python 3.12 | Runs the pipeline | `winget install Python.Python.3.12` |
| ffmpeg | Video/audio assembly | `winget install Gyan.FFmpeg` |
| Pexels API key | Free stock video/photos | https://www.pexels.com/api/ (free, instant) |
| Pixabay API key | Free stock fallback + music | https://pixabay.com/api/docs/ (free, instant) |

Then, from this folder in PowerShell:

```powershell
./setup.ps1
```

This creates a virtual environment, installs Python packages, and downloads the
offline Piper voice model (~60 MB). Run it once.

Finally, copy `config.example.toml` to `config.toml` and paste in your two API keys.

---

## Making a video

1. Write a script. Copy `scripts/example-octopus.md` to `scripts/my-topic.md`
   and edit it. Format:

   ```markdown
   ---
   title: 5 Things You Didn't Know About Octopuses
   description_hook: Three hearts is the least weird thing about them.
   tags: octopus, ocean, marine biology, animal facts
   music: assets/music/curious.mp3
   ---

   ## Hook
   Octopuses have three hearts, blue blood, and can taste with their skin.
   Let's get to know what you don't.
   [[octopus swimming underwater]]

   ## Three hearts
   Two hearts pump blood through the gills. The third pushes it round the body...
   [[octopus close up]] [[deep blue ocean]]
   ```

   - Each `##` heading starts a new scene.
   - Text under a heading is the narration for that scene.
   - `[[search terms]]` are stock-footage queries. Put one or more per scene;
     the scene's screen time is split evenly between them.

2. Drop a music file into `assets/music/` (see that folder's README for free
   sources) and point `music:` at it. Optional — omit the line for no music.

3. Build:

   ```powershell
   ./run.ps1 scripts/my-topic.md
   ```

4. Output lands in `build/my-topic/`:
   - `my-topic.mp4` — the finished video
   - `my-topic_thumbnail.png` — 1280x720 thumbnail
   - `my-topic_metadata.txt` — title, description, tags, chapters to paste into YouTube
   - `my-topic.srt` — caption file (also burned into the video)

5. Review the mp4. If a clip is wrong, change the `[[query]]` and rebuild — cached
   downloads make reruns fast.

---

## Config knobs (`config.toml`)

- `orientation` — `landscape` (1920x1080) or `portrait` (1080x1920 for Shorts)
- `voice.model` — any voice in `pipeline/voices.py` (run `./run.ps1 --list-voices`)
- `voice.length_scale` — >1 slower, <1 faster (1.0 default)
- `captions.enabled`, `captions.max_words_per_line`, `captions.font`
- `music.volume` — 0.0–1.0, default 0.12

---

## Full automation

Once the manual pipeline works, `auto.py` does everything in one command —
pick a script, render, upload to YouTube, archive — and Windows Task Scheduler
runs it on a schedule. See **[AUTOMATION.md](AUTOMATION.md)** and
**[YOUTUBE_SETUP.md](YOUTUBE_SETUP.md)**.

```powershell
.\setup-auto.ps1                                   # Google libs + Ollama (once)
.\.venv\Scripts\python.exe -m pipeline.youtube --auth   # authorise (once)
.\.venv\Scripts\python.exe auto.py --dry-run       # render, don't upload
.\schedule-install.ps1 -PerDay 1                   # 1 video/day, ramp up later
```

Scripts come from `scripts/bank/` (52 hand-written, safe to auto-publish), then
from a local AI model fed by `topics.txt` (those wait in `scripts/review/` for
your ok). Guardrails in `pipeline/guardrails.py` block off-brand or risky topics,
titles, and scripts.

## YouTube / kids-content notes

- This channel format (explainer facts) is **not** "Made for Kids" — mark it
  "No, it's not made for kids" at upload unless a specific video targets under-13s.
- If you ever do make a kids video, YouTube's COPPA rules kick in: no targeted
  ads, no comments, no end screens. And YouTube actively demotes low-effort
  mass-produced kids content, so keep it genuinely original.
- Music and stock must stay license-clean. Pexels/Pixabay assets are safe for
  commercial/monetized use with no attribution required. Only use music you have
  a clear right to (Pixabay, YouTube Audio Library, or your own).

---

## Layout

```
yt-pipeline/
  setup.ps1            one-time environment setup
  run.ps1             wrapper -> python make_video.py
  make_video.py       orchestrator
  config.toml         your keys + settings (git-ignored)
  pipeline/
    script_parser.py  markdown script -> scenes
    tts.py            Piper narration
    visuals.py        Pexels/Pixabay fetch + cache
    captions.py       faster-whisper -> .srt / .ass
    assemble.py       ffmpeg scene build + final mux
    thumbnail.py      Pillow thumbnail
    metadata.py       YouTube metadata file
    voices.py         Piper voice catalog + downloader
    util.py           ffmpeg/ffprobe helpers
  scripts/            your video scripts
  assets/
    music/            your music files
    fonts/            (optional) bundled fonts
  cache/             downloaded stock assets (git-ignored)
  build/             rendered output (git-ignored)
  models/            Piper voice files (git-ignored)
```
