"""Fetch stock footage / photos from Pexels and Pixabay, with a local cache.

Both services allow free commercial use of their assets with no attribution
required. We still record the source in the metadata file as good practice.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

import requests

from .util import CACHE_DIR, PipelineError

_TIMEOUT = 30


@dataclass
class Asset:
    path: Path
    kind: str        # "video" | "image"
    query: str
    source: str      # "pexels" | "pixabay" | "solid"


def _cache_path(query: str, source: str, ext: str) -> Path:
    h = hashlib.sha1(f"{source}:{query}".encode()).hexdigest()[:16]
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return CACHE_DIR / f"{source}_{h}{ext}"


def _download(url: str, dest: Path) -> None:
    with requests.get(url, stream=True, timeout=_TIMEOUT) as r:
        r.raise_for_status()
        with dest.open("wb") as fh:
            for chunk in r.iter_content(1 << 16):
                fh.write(chunk)


def _pexels_video(query: str, key: str, orientation: str) -> Asset | None:
    r = requests.get(
        "https://api.pexels.com/videos/search",
        headers={"Authorization": key},
        params={"query": query, "per_page": 10, "orientation": orientation,
                "size": "large"},
        timeout=_TIMEOUT,
    )
    r.raise_for_status()
    for vid in r.json().get("videos", []):
        mp4s = [f for f in vid.get("video_files", [])
                if f.get("width") and f.get("file_type") == "video/mp4"]
        # prefer the smallest file that is still at least full-HD; else the biggest
        hd = sorted((f for f in mp4s if (f.get("width") or 0) >= 1920
                     and (f.get("height") or 0) >= 1080),
                    key=lambda f: f["width"])
        pick = hd[0] if hd else (max(mp4s, key=lambda f: f["width"]) if mp4s else None)
        if pick:
            dest = _cache_path(query, "pexels", ".mp4")
            if not dest.exists():
                _download(pick["link"], dest)
            return Asset(dest, "video", query, "pexels")
    return None


def _pexels_photo(query: str, key: str, orientation: str) -> Asset | None:
    r = requests.get(
        "https://api.pexels.com/v1/search",
        headers={"Authorization": key},
        params={"query": query, "per_page": 8, "orientation": orientation},
        timeout=_TIMEOUT,
    )
    r.raise_for_status()
    for photo in r.json().get("photos", []):
        url = photo.get("src", {}).get("large2x") or photo.get("src", {}).get("large")
        if url:
            dest = _cache_path(query, "pexels", ".jpg")
            if not dest.exists():
                _download(url, dest)
            return Asset(dest, "image", query, "pexels")
    return None


def _pixabay_video(query: str, key: str) -> Asset | None:
    r = requests.get(
        "https://pixabay.com/api/videos/",
        params={"key": key, "q": query, "per_page": 8, "safesearch": "true"},
        timeout=_TIMEOUT,
    )
    r.raise_for_status()
    for hit in r.json().get("hits", []):
        streams = hit.get("videos", {})
        f = streams.get("large") or streams.get("medium") or streams.get("small")
        if f and f.get("url"):
            dest = _cache_path(query, "pixabay", ".mp4")
            if not dest.exists():
                _download(f["url"], dest)
            return Asset(dest, "video", query, "pixabay")
    return None


def _pixabay_photo(query: str, key: str) -> Asset | None:
    r = requests.get(
        "https://pixabay.com/api/",
        params={"key": key, "q": query, "per_page": 8, "image_type": "photo",
                "safesearch": "true"},
        timeout=_TIMEOUT,
    )
    r.raise_for_status()
    for hit in r.json().get("hits", []):
        url = hit.get("largeImageURL") or hit.get("webformatURL")
        if url:
            dest = _cache_path(query, "pixabay", ".jpg")
            if not dest.exists():
                _download(url, dest)
            return Asset(dest, "image", query, "pixabay")
    return None


_ANIMATION_TERMS = ("animation", "3d animation", "motion graphics")


class VisualFetcher:
    def __init__(self, cfg: dict):
        apis = cfg.get("apis", {})
        self.pexels = apis.get("pexels_key", "").strip()
        self.pixabay = apis.get("pixabay_key", "").strip()
        vcfg = cfg.get("visuals", {})
        self.prefer = vcfg.get("prefer", "video").lower()
        # Prefer real animated/CGI/motion-graphics stock footage over live-action
        # photography or filmed video, when the library actually has it for a
        # given topic - falls back to the plain query (video, then photo) when
        # it doesn't, so this can never turn into "no results" for a niche topic.
        self.prefer_animation = bool(vcfg.get("prefer_animation", True))
        orient = cfg.get("project", {}).get("orientation", "landscape").lower()
        self.orientation = "portrait" if orient.startswith("port") else "landscape"
        if not self.pexels and not self.pixabay:
            raise PipelineError(
                "No API keys set. Add pexels_key and/or pixabay_key to config.toml, "
                "or run with --no-stock to render plain slides."
            )
        self._counter = 0

    def fetch(self, query: str) -> Asset:
        self._counter += 1
        want_video = self.prefer == "video" or (
            self.prefer == "mix" and self._counter % 2 == 1
        )
        chain = []
        if want_video and self.prefer_animation:
            # Try one animation-biased phrasing before anything else. Only one
            # term, not all three - a scene-by-scene budget, not a full sweep,
            # to keep API call counts sane across a 6-11 scene render.
            term = _ANIMATION_TERMS[self._counter % len(_ANIMATION_TERMS)]
            anim_query = f"{query} {term}"
            if self.pexels:
                chain.append(lambda: _pexels_video(anim_query, self.pexels, self.orientation))
            if self.pixabay:
                chain.append(lambda: _pixabay_video(anim_query, self.pixabay))
        if want_video:
            if self.pexels:
                chain.append(lambda: _pexels_video(query, self.pexels, self.orientation))
            if self.pixabay:
                chain.append(lambda: _pixabay_video(query, self.pixabay))
        if self.pexels:
            chain.append(lambda: _pexels_photo(query, self.pexels, self.orientation))
        if self.pixabay:
            chain.append(lambda: _pixabay_photo(query, self.pixabay))
        if not want_video:  # try video only as a last resort
            if self.pexels:
                chain.append(lambda: _pexels_video(query, self.pexels, self.orientation))
            if self.pixabay:
                chain.append(lambda: _pixabay_video(query, self.pixabay))

        last_err: Exception | None = None
        for step in chain:
            try:
                asset = step()
                if asset:
                    return asset
            except requests.HTTPError as e:
                last_err = e
            except requests.RequestException as e:
                last_err = e
        if last_err:
            raise PipelineError(f"stock lookup failed for {query!r}: {last_err}")
        raise PipelineError(
            f"no stock results for {query!r}. Try a broader or more common phrase."
        )
