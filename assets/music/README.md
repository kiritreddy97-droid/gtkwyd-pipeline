# Music

**See ../../MUSIC_SETUP.md for the step-by-step guide.**

Drop `.mp3` files into:
- `videos/`  — used for long videos
- `shorts/`  — used for Shorts
- or straight into this folder — used for both

The pipeline picks one at random per render, loops it, ducks it under the
narration, and fades it out. No `music:` line needed in scripts (you can still add
one to force a specific track).

## Free, monetization-safe sources

| Source | Notes |
|---|---|
| **YouTube Audio Library** (studio.youtube.com > Audio library) | Free, cleared for monetization. Some tracks need attribution — the library tells you. |
| **Pixabay Music** (pixabay.com/music) | Free for commercial use, no attribution required. |
| **Incompetech** (incompetech.com) by Kevin MacLeod | Free with attribution (CC BY). Put the credit line in your video description. |
| **Free Music Archive** (freemusicarchive.org) | Filter to CC licenses; check each track's terms. |

## Rules of thumb

- Never use a track you cannot point to a clear license for. Copyright claims on
  music are the #1 reason faceless videos get demonetized.
- Keep music low (config `music.volume = 0.10`-`0.14`) so narration stays clear.
- Instrumental only — lyrics fight the voiceover.
- One calm, loopable track per video is plenty; the pipeline loops and fades it.
