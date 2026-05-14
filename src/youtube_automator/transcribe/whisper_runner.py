"""Local transcription pipeline for building the style corpus.

Flow:
  1. yt-dlp downloads audio from a list of the user's own YouTube videos.
  2. faster-whisper transcribes locally (Spanish, base/medium model).
  3. Output saved to data/corpus/transcripts/<video_id>.txt.

This runs once (or occasionally, when the user wants to refresh the corpus
with newer videos). Not part of the per-video pipeline.
"""

from __future__ import annotations

from pathlib import Path


def transcribe_channel_videos(
    video_urls: list[str],
    *,
    language: str = "es",
    model_size: str = "medium",
) -> list[Path]:
    """Download + transcribe a batch of user-owned videos.

    NOTE: stub. Will use yt-dlp to grab bestaudio, then faster-whisper.
    Idempotent: skips already-transcribed video IDs.
    """
    raise NotImplementedError("Stub — implement yt-dlp + faster-whisper pipeline")
