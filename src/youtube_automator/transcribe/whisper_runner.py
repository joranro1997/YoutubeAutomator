"""Local transcription pipeline for building the style corpus.

Flow per video URL:
  1. yt-dlp downloads bestaudio into data/tmp/<video_id>.m4a (or .opus).
  2. faster-whisper transcribes locally to plain text.
  3. Output saved to data/corpus/transcripts/<video_id>.txt
     and metadata to data/corpus/transcripts/<video_id>.json
     (id, title, channel, duration, transcription model, language).

Idempotent: any video_id already present under transcripts/ is skipped.

This is a one-off / occasional job — NOT part of the per-video pipeline.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

import yt_dlp
from faster_whisper import WhisperModel

from ..paths import TMP_DIR, TRANSCRIPTS_DIR, ensure_dirs


_YT_ID_RE = re.compile(r"(?:v=|youtu\.be/|/shorts/)([A-Za-z0-9_-]{11})")


@dataclass
class TranscriptionResult:
    video_id: str
    transcript_path: Path
    metadata_path: Path
    skipped: bool


def _extract_video_id(url: str) -> str:
    m = _YT_ID_RE.search(url)
    if not m:
        # Allow callers to pass bare IDs too.
        if re.fullmatch(r"[A-Za-z0-9_-]{11}", url):
            return url
        raise ValueError(f"Cannot extract YouTube video id from: {url!r}")
    return m.group(1)


def _download_audio(url: str, video_id: str) -> tuple[Path, dict]:
    """Download bestaudio to data/tmp/. Returns (audio_path, info_dict)."""
    out_template = str(TMP_DIR / f"{video_id}.%(ext)s")
    ydl_opts = {
        "format": "bestaudio/best",
        "outtmpl": out_template,
        "quiet": True,
        "noprogress": True,
        "noplaylist": True,
        # Keep original audio; faster-whisper handles m4a/opus/webm fine via av/ffmpeg.
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)
    # yt-dlp can change extension; locate the file by id prefix.
    candidates = list(TMP_DIR.glob(f"{video_id}.*"))
    audio_files = [p for p in candidates if p.suffix.lower() not in {".json", ".txt"}]
    if not audio_files:
        raise FileNotFoundError(f"Audio download for {video_id} not found in {TMP_DIR}")
    return audio_files[0], info


def _transcribe_file(
    audio_path: Path,
    *,
    model: WhisperModel,
    language: str,
) -> str:
    segments, _info = model.transcribe(
        str(audio_path),
        language=language,
        beam_size=5,
        vad_filter=True,
    )
    return " ".join(seg.text.strip() for seg in segments).strip()


def transcribe_urls(
    urls: list[str],
    *,
    language: str = "en",
    model_size: str = "small",
    device: str = "cpu",
    compute_type: str = "int8",
    cleanup_audio: bool = True,
) -> list[TranscriptionResult]:
    """Transcribe a batch of YouTube URLs.

    Defaults are tuned for an M-series Mac running CPU + int8 (fast, decent
    quality, no GPU needed). For higher quality use model_size="medium" and
    accept ~3x slower per video.
    """
    ensure_dirs()
    results: list[TranscriptionResult] = []
    model: WhisperModel | None = None  # lazy: only load when we have new work

    for url in urls:
        try:
            video_id = _extract_video_id(url)
        except ValueError as e:
            print(f"[skip] {e}")
            continue

        transcript_path = TRANSCRIPTS_DIR / f"{video_id}.txt"
        meta_path = TRANSCRIPTS_DIR / f"{video_id}.json"
        if transcript_path.exists():
            results.append(
                TranscriptionResult(
                    video_id=video_id,
                    transcript_path=transcript_path,
                    metadata_path=meta_path,
                    skipped=True,
                )
            )
            print(f"[skip] {video_id} already transcribed")
            continue

        if model is None:
            print(f"[load] faster-whisper model={model_size} device={device} compute={compute_type}")
            model = WhisperModel(model_size, device=device, compute_type=compute_type)

        print(f"[dl  ] {video_id}")
        audio_path, info = _download_audio(url, video_id)
        try:
            print(f"[asr ] {video_id} ({info.get('duration', '?')}s)")
            text = _transcribe_file(audio_path, model=model, language=language)
            transcript_path.write_text(text, encoding="utf-8")
            meta_path.write_text(
                json.dumps(
                    {
                        "video_id": video_id,
                        "title": info.get("title"),
                        "channel": info.get("channel"),
                        "duration_s": info.get("duration"),
                        "upload_date": info.get("upload_date"),
                        "language": language,
                        "model": model_size,
                        "compute_type": compute_type,
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
            print(f"[ok  ] {video_id} -> {transcript_path.name} ({len(text)} chars)")
            results.append(
                TranscriptionResult(
                    video_id=video_id,
                    transcript_path=transcript_path,
                    metadata_path=meta_path,
                    skipped=False,
                )
            )
        finally:
            if cleanup_audio and audio_path.exists():
                audio_path.unlink()

    return results


def transcribe_from_file(
    urls_file: Path,
    **kwargs,
) -> list[TranscriptionResult]:
    """Convenience: read URLs from a file (one per line, blanks/# ignored)."""
    urls: list[str] = []
    for line in urls_file.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        urls.append(s)
    return transcribe_urls(urls, **kwargs)
