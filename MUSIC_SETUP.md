# Adding music (copyright-safe)

The pipeline adds a quiet background track to every video and Short automatically,
picked at random from your library. You supply the tracks once.

**Use the YouTube Audio Library.** It is YouTube's own catalogue, so tracks from it
never trigger a copyright claim on YouTube. This is the safest possible source.

## Step by step (~10 minutes, one time)

1. Go to **https://studio.youtube.com** and sign in with the channel account.
2. Left menu → **Audio Library** (near the bottom).
3. Tab: **Music**.
4. Set the filter **Attribution required = No** (so you don't have to add credit
   lines). Optional filters: Mood = Calm / Inspirational / Bright; Genre =
   Ambient / Cinematic; Duration = 1-3 min.
5. Preview a few. For each one you like, click the **download arrow** on the right.
6. Download about **8-12 tracks**. Aim for a consistent calm, curious feel.

## Where to put them

Drop the downloaded `.mp3` files into:

```
C:\Users\kirit\Videos\yt-pipeline\assets\music\videos\    <- for long videos
C:\Users\kirit\Videos\yt-pipeline\assets\music\shorts\    <- for Shorts (punchier is fine)
```

If you don't want to split them, just put everything in
`assets\music\` and the pipeline uses that pool for both.

That's it. The next render picks one at random, loops it to length, ducks it under
the narration, and fades it out at the end.

## Rules

- **Only** YouTube Audio Library, or music you have a written licence for. Never
  "royalty-free" tracks from random sites, never anything from Spotify/Apple, never
  "trending audio". One wrong track can get the channel demonetised.
- Instrumental only. Lyrics fight the narration.
- Keep `config.toml` → `[music] volume` around `0.10`-`0.14`. The narration must
  stay clearly on top.
- To turn music off entirely: empty the `assets\music` folders, or set
  `volume = 0`.
