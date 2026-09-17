# One-time Instagram cross-post setup

You do this once. Takes about 15 minutes. Like YouTube, no password is ever
given to the pipeline — you approve access on Meta's own screen, and it hands
the pipeline a revocable access token.

What this buys you: ~1 hour after each full video goes live on YouTube, a
~20s glimpse (hook + a highlight clip + a "watch the full video on YouTube,
link in bio" outro) auto-posts to Instagram as a Reel, with a matching cover
image. Shorts are skipped — they're already short-form. See `AUTOMATION.md`
for how the whole pipeline fits together.

## 1. Make sure your Instagram is a Business or Creator account

1. Instagram app → your profile → **Edit profile** → **Switch to professional
   account** (if you haven't already) → pick **Creator** or **Business**.
2. This is required — the Graph API can't post to a personal account.

## 2. Create a Meta Developer app

1. Go to https://developers.facebook.com/ and log in with the Facebook
   account linked to your Instagram (or link one — Instagram professional
   accounts are tied to a Facebook account even if you never use Facebook
   itself).
2. **My Apps → Create App**. Type: **Business**. Name it e.g. `gtkwyd-crosspost`.
   Create.

## 3. Add Instagram and connect your account

1. In the app dashboard, find **Instagram** under "Add products to your app"
   and set it up.
2. Under the Instagram product's **API setup with Instagram login** (Meta has
   renamed this flow a few times — look for anything mentioning "Business
   Login for Instagram" or "Instagram API with Instagram Login" if the exact
   label differs), click through to connect your Instagram account directly.
3. Approve the permissions it asks for — you need at least
   `instagram_business_basic` and `instagram_business_content_publish`.
4. This step generates a **short-lived access token** and shows your
   **Instagram Business Account ID** (a numeric ID) — copy both, you'll need
   them in a minute.

## 4. Exchange for a long-lived token

Short-lived tokens expire in ~1 hour. Exchange it for a long-lived one
(~60 days) with one request — replace the placeholders and run this in
PowerShell:

```powershell
$appId = "YOUR_APP_ID"          # App dashboard -> Settings -> Basic
$appSecret = "YOUR_APP_SECRET"  # same page - keep this private
$shortToken = "THE_SHORT_LIVED_TOKEN_FROM_STEP_3"

Invoke-RestMethod "https://graph.facebook.com/v21.0/oauth/access_token?grant_type=fb_exchange_token&client_id=$appId&client_secret=$appSecret&fb_exchange_token=$shortToken"
```

The response's `access_token` is your long-lived token. It's valid ~60 days —
see **Renewing** below for how to keep it alive without redoing this whole
flow.

## 5. Store the credentials as GitHub Secrets

Same pattern as the YouTube secrets — never paste these into a chat with
Claude or anyone else. From the project folder in PowerShell:

```powershell
gh secret set IG_USER_ID --body "YOUR_INSTAGRAM_BUSINESS_ACCOUNT_ID"
gh secret set IG_ACCESS_TOKEN --body "YOUR_LONG_LIVED_TOKEN"
```

(Or GitHub web UI: repo → **Settings → Secrets and variables → Actions → New
repository secret**.)

That's it — the next `instagram.yml` scheduled run will pick them up
automatically. Nothing else needs redeploying.

## 6. Set your handle (optional but recommended)

In `config.example.toml`, under `[social]`:

```toml
[social]
youtube_handle = "@YourChannelHandle"
```

Shown on the Instagram CTA outro card and in the post caption.

## 7. Test it

Stage a glimpse manually against any already-rendered video:

```powershell
.\.venv\Scripts\python.exe -m pipeline.instagram build "build\some-slug\some-slug.mp4" "test-glimpse.mp4"
```

check `test-glimpse.mp4` looks right, then trigger the poster workflow by
hand once you have a real staged entry (`instagram_glimpse_url` in
`history.jsonl`, which auto.py adds automatically after any real video
upload once secrets are set):

```powershell
gh workflow run instagram
```

## Renewing the token

Long-lived tokens last ~60 days and **do not auto-refresh**. Before it
expires, get a new one from the current one (no need to redo steps 1-4):

```powershell
$token = "YOUR_CURRENT_LONG_LIVED_TOKEN"
Invoke-RestMethod "https://graph.facebook.com/v21.0/refresh_access_token?grant_type=ig_refresh_token&access_token=$token"
```

Take the new `access_token` from the response and re-run:

```powershell
gh secret set IG_ACCESS_TOKEN --body "THE_NEW_TOKEN"
```

Put a reminder on your calendar for ~day 50 — if the token lapses, glimpses
just keep staging (harmless) but stop posting until you refresh it.

## Notes

- **Posting requires a public URL.** The Graph API can't accept an uploaded
  file directly for video — it fetches the clip from a URL. The pipeline
  hosts each glimpse + cover as a GitHub Release asset (same trick already
  used for the music library) so this works with zero extra hosting cost.
- **No app review needed** as long as you're only posting to your own
  connected Instagram account (the one used to create the app) — that's
  exactly this setup.
- Keep `IG_ACCESS_TOKEN` private. It's stored only as an encrypted GitHub
  Secret, never in the repo.
