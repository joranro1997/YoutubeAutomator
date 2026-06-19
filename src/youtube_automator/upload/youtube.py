"""YouTube Data API v3 upload client.

OAuth 2.0 desktop-app flow:
- First run opens a browser tab for user consent and caches the token.
- Subsequent runs reuse the token (auto-refresh via the long-lived refresh
  token; the user is never prompted again until they revoke access).

What we upload:
- The MP4 (resumable upload, handles large files robustly).
- The thumbnail PNG (separate API call to thumbnails().set()).
- All the metadata fields (title, description, tags, categoryId, default
  language, "made for kids", and optionally a scheduled publish time).

Quota cost: a single video upload + thumbnail = ~1700 units. The default
daily quota is 10,000 units, so up to ~5 video uploads per day. For 2
videos per week per game (LoM + LoE = 4/week) we're well within limits.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload

from ..config import GameConfig, get_env
from ..metadata.generator import VideoMetadata
from ..paths import REPO_ROOT, TMP_DIR

# The YouTube thumbnails().set() endpoint enforces a 2 MiB hard limit. We
# leave a small margin so a few bytes of JSON envelope still fit.
THUMBNAIL_MAX_BYTES = 2 * 1024 * 1024 - 4096


_log = logging.getLogger(__name__)


SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube",   # required to set thumbnail
]


@dataclass
class UploadResult:
    video_id: str
    url: str


def _abs(path_str: str) -> Path:
    p = Path(path_str)
    return p if p.is_absolute() else (REPO_ROOT / p)


def _load_credentials() -> Credentials:
    env = get_env()
    client_secrets = _abs(env.youtube_client_secrets_path)
    token_path = _abs(env.youtube_token_path)

    if not client_secrets.exists():
        raise FileNotFoundError(
            f"OAuth client secrets not found at {client_secrets}. "
            "Download a Desktop-app OAuth Client ID JSON from Google Cloud Console "
            "and place it there. See README setup section."
        )

    creds: Credentials | None = None
    if token_path.exists():
        try:
            creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)
        except Exception as e:  # noqa: BLE001
            _log.warning("could not load cached token (%s) — re-authorising", e)
            creds = None

    if creds and creds.valid:
        return creds

    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            token_path.parent.mkdir(parents=True, exist_ok=True)
            token_path.write_text(creds.to_json(), encoding="utf-8")
            return creds
        except Exception as e:  # noqa: BLE001
            _log.warning("token refresh failed (%s) — re-authorising", e)

    # Interactive consent. Opens a browser tab; user authorises; the local
    # server captures the OAuth code and the flow returns Credentials.
    flow = InstalledAppFlow.from_client_secrets_file(str(client_secrets), SCOPES)
    creds = flow.run_local_server(port=0, prompt="consent")
    token_path.parent.mkdir(parents=True, exist_ok=True)
    token_path.write_text(creds.to_json(), encoding="utf-8")
    return creds


def _service():
    return build("youtube", "v3", credentials=_load_credentials())


def _format_publish_at(when: datetime | None) -> str | None:
    if when is None:
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    # YouTube wants RFC 3339.
    return when.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _shrink_thumbnail(src: Path) -> Path:
    """Return a path to a thumbnail <2 MiB. Re-saves as JPEG if PNG too big.

    YouTube's thumbnails().set() endpoint rejects anything >2 MiB outright
    (MediaUploadSizeError). PNG is lossless and can balloon past 2 MiB on
    1920×1080 art; JPEG quality 92 typically halves that and is visually
    indistinguishable at YouTube's display sizes.
    """
    if src.stat().st_size <= THUMBNAIL_MAX_BYTES:
        return src
    try:
        from PIL import Image  # type: ignore
    except ImportError:
        _log.warning(
            "thumbnail %s is %.2f MiB (>2 MiB) and Pillow is not installed — "
            "skipping thumbnail",
            src.name, src.stat().st_size / (1024 * 1024),
        )
        return src  # caller will see the size error and skip gracefully

    img = Image.open(src).convert("RGB")
    TMP_DIR.mkdir(parents=True, exist_ok=True)
    out = TMP_DIR / f"{src.stem}_thumb.jpg"
    # Try descending quality until under the limit.
    for quality in (92, 85, 78, 70, 60):
        img.save(out, "JPEG", quality=quality, optimize=True)
        if out.stat().st_size <= THUMBNAIL_MAX_BYTES:
            _log.info(
                "thumbnail shrunk %s -> %s (q=%d, %.2f -> %.2f MiB)",
                src.name, out.name, quality,
                src.stat().st_size / (1024 * 1024),
                out.stat().st_size / (1024 * 1024),
            )
            return out
    _log.warning("thumbnail still >2 MiB at quality 60 — using best effort")
    return out


def _set_thumbnail(yt, video_id: str, thumb_path: Path) -> None:
    """Best-effort thumbnail upload. Never raises; logs the failure mode."""
    try:
        prepared = _shrink_thumbnail(thumb_path)
        yt.thumbnails().set(
            videoId=video_id,
            media_body=MediaFileUpload(str(prepared)),
        ).execute()
        _log.info("thumbnail set")
    except Exception as e:  # noqa: BLE001 — never fail the upload over this
        _log.warning("thumbnail set failed: %s: %s", type(e).__name__, e)


def _add_to_playlist(yt, video_id: str, playlist_id: str) -> None:
    """Best-effort playlist insertion. Never raises."""
    if not playlist_id:
        return
    try:
        yt.playlistItems().insert(
            part="snippet",
            body={
                "snippet": {
                    "playlistId": playlist_id,
                    "resourceId": {"kind": "youtube#video", "videoId": video_id},
                }
            },
        ).execute()
        _log.info("added video to playlist %s", playlist_id)
    except Exception as e:  # noqa: BLE001
        _log.warning("playlist insert failed: %s: %s", type(e).__name__, e)


def update_video_metadata(
    yt,
    video_id: str,
    *,
    title: str,
    description: str,
    tags: list[str],
    category_id: str,
) -> None:
    """Update an ALREADY-UPLOADED video's snippet (title/description/tags/
    category). videos.update REPLACES the snippet, so all four fields are sent
    together — omitting one would CLEAR it on the live video. Used to push
    regenerated metadata onto a published video."""
    yt.videos().update(
        part="snippet",
        body={
            "id": video_id,
            "snippet": {
                "title": title[:100],            # YouTube hard limit
                "description": description[:5000],  # YouTube hard limit
                "tags": tags,
                "categoryId": category_id,
            },
        },
    ).execute()
    _log.info("updated snippet for %s", video_id)


def upload(
    *,
    video_path: Path,
    thumbnail_path: Path | None,
    metadata: VideoMetadata,
    chosen_title: str,
    game: GameConfig,
    publish_at: datetime | None = None,
    privacy_status: str = "private",   # "private" | "unlisted" | "public"
    made_for_kids: bool | None = None,
) -> UploadResult:
    """Upload a video, attach a thumbnail, add it to the game's playlist
    and (optionally) schedule.

    Defaults to private so the user can review on YouTube Studio before
    going live. Passing `publish_at` flips the status to "private" with
    a scheduled publish time (YouTube requires private for scheduled
    publishing).

    Side-effect order is deliberate: the video upload happens first; once
    YouTube has assigned a video_id everything else (thumbnail, playlist)
    runs as best-effort. The function ALWAYS returns the UploadResult on
    a successful video upload so the caller can write `uploaded.json`
    immediately and never re-upload on the next watch cycle.
    """
    if not video_path.exists():
        raise FileNotFoundError(f"video not found: {video_path}")
    if thumbnail_path and not thumbnail_path.exists():
        raise FileNotFoundError(f"thumbnail not found: {thumbnail_path}")

    yt = _service()

    if made_for_kids is None:
        made_for_kids = (game.youtube.default_audience or "no").lower() == "yes"

    status: dict = {
        "privacyStatus": privacy_status,
        "selfDeclaredMadeForKids": made_for_kids,
        # We do not use AI-altered or synthetic media; declare it up-front
        # so YouTube does not flag the upload for the new 2024 disclosure.
        "containsSyntheticMedia": False,
    }
    publish_iso = _format_publish_at(publish_at)
    if publish_iso:
        status["privacyStatus"] = "private"   # required for scheduled
        status["publishAt"] = publish_iso

    body = {
        "snippet": {
            "title": chosen_title[:100],   # YouTube hard limit
            "description": metadata.description[:5000],   # YouTube hard limit
            "tags": metadata.tags,
            "categoryId": game.youtube.default_category_id,
            "defaultLanguage": game.youtube.default_language,
            "defaultAudioLanguage": game.youtube.default_language,
        },
        "status": status,
    }

    media = MediaFileUpload(
        str(video_path),
        chunksize=8 * 1024 * 1024,   # 8 MiB
        resumable=True,
        mimetype="video/*",
    )
    request = yt.videos().insert(part="snippet,status", body=body, media_body=media)

    _log.info("starting upload of %s (%.1f MiB)", video_path.name, video_path.stat().st_size / 1e6)
    response = None
    while response is None:
        try:
            status_resp, response = request.next_chunk()
            if status_resp:
                pct = int(status_resp.progress() * 100)
                _log.info("  upload progress: %d%%", pct)
        except HttpError as e:
            if e.resp.status in (500, 502, 503, 504):
                _log.warning("transient %d, retrying", e.resp.status)
                continue
            raise

    video_id = response["id"]
    _log.info("upload complete: video_id=%s", video_id)

    # Best-effort post-upload — never raises. The caller can persist
    # uploaded.json the moment we return, with no risk of double-uploading
    # on a later retry due to a thumbnail/playlist hiccup.
    if thumbnail_path:
        _set_thumbnail(yt, video_id, thumbnail_path)
    _add_to_playlist(yt, video_id, game.youtube.playlist_id)

    return UploadResult(
        video_id=video_id, url=f"https://www.youtube.com/watch?v={video_id}"
    )
