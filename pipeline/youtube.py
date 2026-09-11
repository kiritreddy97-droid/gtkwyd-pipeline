"""YouTube upload via the official Data API v3.

Auth model: YOU authorise the app once through Google's own consent screen
(`python -m pipeline.youtube --auth`). A refresh token is stored locally in
youtube_token.json. No password is ever seen by this code.

Files (both git-ignored, keep them private):
  client_secret.json  - downloaded from Google Cloud Console (see YOUTUBE_SETUP.md)
  youtube_token.json  - created on first --auth, refreshed automatically
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

from .util import ROOT, PipelineError

TOKEN = ROOT / "youtube_token.json"


def _find_client_secret() -> Path:
    """Accept client_secret.json, or any client_secret*.json / *.json.json that
    Windows may have produced, or the raw Google download name."""
    exact = ROOT / "client_secret.json"
    if exact.exists():
        return exact
    candidates = sorted(
        p for p in ROOT.glob("client_secret*.json*")
        if p.suffix in (".json",) or p.name.endswith(".json.json")
    )
    candidates += sorted(ROOT.glob("*.apps.googleusercontent.com.json"))
    return candidates[0] if candidates else exact


CLIENT_SECRET = _find_client_secret()
SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube",
]
CATEGORY_EDUCATION = "27"


def _require_libs():
    try:
        from google.auth.transport.requests import Request  # noqa
        from google.oauth2.credentials import Credentials  # noqa
        from google_auth_oauthlib.flow import InstalledAppFlow  # noqa
        from googleapiclient.discovery import build  # noqa
        from googleapiclient.http import MediaFileUpload  # noqa
    except ImportError as e:
        raise PipelineError(
            "Google API libraries missing. Run setup-auto.ps1, or:\n"
            "  .venv\\Scripts\\pip install google-api-python-client "
            "google-auth-oauthlib google-auth-httplib2"
        ) from e


def get_service(interactive: bool = True):
    _require_libs()
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build

    creds = None
    if TOKEN.exists():
        creds = Credentials.from_authorized_user_file(str(TOKEN), SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        elif interactive:
            secret = _find_client_secret()
            if not secret.exists():
                raise PipelineError(
                    "No OAuth client file found in the project folder. Follow "
                    "YOUTUBE_SETUP.md, then drop the downloaded JSON here. Any of "
                    "client_secret.json, client_secret.json.json, or the original "
                    "*.apps.googleusercontent.com.json name will work."
                )
            print(f"  using {secret.name}")
            flow = InstalledAppFlow.from_client_secrets_file(str(secret), SCOPES)
            creds = flow.run_local_server(port=0, prompt="consent")
        else:
            raise PipelineError(
                "YouTube token missing/expired and no refresh token. "
                "Run  python -m pipeline.youtube --auth  once."
            )
        TOKEN.write_text(creds.to_json(), encoding="utf-8")
    return build("youtube", "v3", credentials=creds, cache_discovery=False)


def channel_title(yt) -> str:
    resp = yt.channels().list(part="snippet", mine=True).execute()
    items = resp.get("items", [])
    return items[0]["snippet"]["title"] if items else "(unknown)"


def upload(video: Path, *, title: str, description: str, tags: list[str],
           privacy: str = "private", made_for_kids: bool = False,
           category_id: str = CATEGORY_EDUCATION, thumbnail: Path | None = None,
           publish_at_iso: str | None = None, playlist_id: str | None = None) -> str:
    import socket

    from googleapiclient.http import MediaFileUpload

    socket.setdefaulttimeout(180)  # never let a chunk hang forever
    yt = get_service(interactive=False)

    status: dict = {
        "privacyStatus": "private" if publish_at_iso else privacy,
        "selfDeclaredMadeForKids": made_for_kids,
        "madeForKids": made_for_kids,
    }
    if publish_at_iso:
        status["publishAt"] = publish_at_iso

    body = {
        "snippet": {
            "title": title[:100],
            "description": description[:4900],
            "tags": tags[:60],
            "categoryId": category_id,
        },
        "status": status,
    }

    media = MediaFileUpload(str(video), chunksize=8 * 1024 * 1024, resumable=True)
    req = yt.videos().insert(part="snippet,status", body=body, media_body=media)

    response = None
    tries = 0
    while response is None:
        try:
            _, response = req.next_chunk()
        except Exception as e:  # transient network / 5xx
            tries += 1
            if tries > 5:
                raise PipelineError(f"upload failed after retries: {e}") from e
            time.sleep(2 ** tries)
    video_id = response["id"]

    if thumbnail and Path(thumbnail).exists():
        try:
            yt.thumbnails().set(
                videoId=video_id,
                media_body=MediaFileUpload(str(thumbnail)),
            ).execute()
        except Exception as e:
            print(f"  thumbnail upload skipped: {e}")

    if playlist_id:
        try:
            yt.playlistItems().insert(
                part="snippet",
                body={"snippet": {"playlistId": playlist_id,
                                  "resourceId": {"kind": "youtube#video",
                                                 "videoId": video_id}}},
            ).execute()
        except Exception as e:
            print(f"  playlist add skipped: {e}")

    return video_id


if __name__ == "__main__":
    if "--auth" in sys.argv:
        svc = get_service(interactive=True)
        print(f"Authorised. Channel: {channel_title(svc)}")
        print(f"Token saved to {TOKEN}")
    else:
        print("usage: python -m pipeline.youtube --auth")
