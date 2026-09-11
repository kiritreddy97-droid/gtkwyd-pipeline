# One-time YouTube auto-upload setup

You do this once. It takes about 10 minutes. No password is ever given to the
script — you approve access on Google's own screen, and Google hands the script a
revocable token.

## 1. Create a Google Cloud project

1. Go to https://console.cloud.google.com/
2. Top bar → project dropdown → **New Project**. Name it e.g. `gtkwyd-uploader`. Create.
3. Make sure that new project is selected in the top bar.

## 2. Enable the YouTube Data API

1. Go to https://console.cloud.google.com/apis/library/youtube.googleapis.com
2. Click **Enable**.

## 3. Configure the consent screen

1. https://console.cloud.google.com/apis/credentials/consent
2. User type: **External** → Create.
3. Fill the required fields (app name, your email). Skip everything optional. Save.
4. **Scopes** page: Save and continue (you don't need to add any here).
5. **Test users** → Add users → add your own Google account (the one that owns the
   YouTube channel). Save.
6. You can leave the app in "Testing" mode. Testing-mode refresh tokens for your
   own test-user account keep working; if uploads ever stop with an auth error,
   just re-run the `--auth` command below.

## 4. Create the OAuth client

1. https://console.cloud.google.com/apis/credentials
2. **Create credentials → OAuth client ID**.
3. Application type: **Desktop app**. Name: `desktop`. Create.
4. In the popup, click **Download JSON**.
5. Rename the downloaded file to **`client_secret.json`** and put it in this folder:
   `C:\Users\kirit\Videos\yt-pipeline\client_secret.json`

## 5. Authorise the pipeline

In PowerShell, from the project folder:

```powershell
.\.venv\Scripts\python.exe -m pipeline.youtube --auth
```

A browser window opens. Sign in with the channel's Google account, click through
the "Google hasn't verified this app" warning (**Advanced → Go to … (unsafe)** —
this is expected for your own testing-mode app), and allow access.

On success it prints your channel name and writes `youtube_token.json`. Done.

## 6. Test

```powershell
.\.venv\Scripts\python.exe auto.py --dry-run
```

renders a video but does not upload. Then a real run:

```powershell
.\.venv\Scripts\python.exe auto.py
```

## Notes, limits, and safety

- **Upload quota.** The YouTube Data API gives each project 10,000 quota units per
  day. One upload costs ~1,600 units, so the ceiling is about **6 uploads/day**.
  Four per day is fine.
- **New-channel limits.** Fresh channels are limited to ~10–15 uploads/day and
  may be restricted to non-public until you verify a phone number in YouTube
  settings. Verify it before switching to public.
- **`config.toml` → `[auto] visibility`.** Set to `"private"` or `"unlisted"`
  while you build trust in the output, then `"public"`.
- **`[auto] require_review_for_ai`.** Leave `true`. Hand-written bank scripts
  auto-publish; AI-written ones wait in `scripts/review/` for you to move into
  `scripts/ready/`.
- **Revoking access.** https://myaccount.google.com/permissions → remove the app.
  Or just delete `youtube_token.json`.
- Keep `client_secret.json` and `youtube_token.json` private. Both are
  git-ignored.
