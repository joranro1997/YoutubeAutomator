"""Phase 3 — the edit plan.

`yta cut` produces an `EditPlan`: the single source of truth the Premiere
ExtendScript generator consumes. It is computed entirely OUTSIDE Premiere
(ffmpeg + Python) so it is fast, deterministic and unit-testable:

  1. Probe each recorded fragment for duration.
  2. Run ffmpeg `silencedetect` on each fragment's audio.
  3. Invert silences -> keep-segments (with margin / min-keep filtering).
  4. Compute the timeline: gameplay duration, promo insertion point
     (snapped so it never splits a sentence), total duration.
  5. If the game has a promo, capture the promo's RIGID internal structure
     (video continuous, audio with the deliberate ~0.4s code excision)
     from a describe-dump of the real template, so the generator can
     reproduce it verbatim wherever the block lands.

The plan is JSON-persisted and meant to be eyeballed before rendering.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

from pydantic import BaseModel, Field

from ..config import GameConfig
from ..paths import TMP_DIR

# Recorded-fragment extensions we accept, ordered by preference irrelevant.
FRAGMENT_EXTS = {".mp4", ".mov", ".mkv", ".m4v", ".avi"}


# --------------------------------------------------------------------------- #
# ffmpeg / ffprobe discovery (winget installs aren't on PATH for live shells)
# --------------------------------------------------------------------------- #
def _find_tool(name: str) -> str:
    """Locate ffmpeg/ffprobe: PATH first, then the WinGet shim/package dirs."""
    found = shutil.which(name)
    if found:
        return found
    import os

    local = os.getenv("LOCALAPPDATA")
    if local:
        links = Path(local) / "Microsoft" / "WinGet" / "Links" / f"{name}.exe"
        if links.exists():
            return str(links)
        pkgs = Path(local) / "Microsoft" / "WinGet" / "Packages"
        if pkgs.exists():
            hits = list(pkgs.glob(f"Gyan.FFmpeg*/**/bin/{name}.exe"))
            if hits:
                return str(hits[0])
    raise FileNotFoundError(
        f"{name} not found. Install ffmpeg (winget install Gyan.FFmpeg) "
        f"or set it on PATH."
    )


def ffmpeg_bin() -> str:
    return _find_tool("ffmpeg")


def ffprobe_bin() -> str:
    return _find_tool("ffprobe")


def probe_duration_sec(media: Path) -> float:
    out = subprocess.run(
        [
            ffprobe_bin(),
            "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=nw=1:nk=1",
            str(media),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return float(out.stdout.strip())


# --------------------------------------------------------------------------- #
# Models
# --------------------------------------------------------------------------- #
class KeepSegment(BaseModel):
    """A span of a source fragment to keep (silence trimmed around it)."""

    src_in_sec: float
    src_out_sec: float

    @property
    def duration_sec(self) -> float:
        return round(self.src_out_sec - self.src_in_sec, 4)


class Fragment(BaseModel):
    index: int
    path: str
    probe_duration_sec: float
    keep_segments: list[KeepSegment] = Field(default_factory=list)

    @property
    def kept_duration_sec(self) -> float:
        return round(sum(k.duration_sec for k in self.keep_segments), 4)


class PromoSubclip(BaseModel):
    """One piece of the rigid promo block, in block-relative time.

    The promo is NOT a single clip: video is continuous while audio has a
    deliberate excision at the spoken affiliate code. We freeze every piece's
    block-relative placement and its source in/out so the generator can shift
    the whole block by a delta and preserve the cut exactly.
    """

    track_role: str            # "content_video" | "gameplay_audio"
    rel_start_sec: float       # start, relative to block start (0)
    rel_end_sec: float
    src_in_sec: float
    src_out_sec: float


class PromoPlan(BaseModel):
    present: bool = False
    asset_path: str = ""
    block_duration_sec: float = 0.0
    # The promo's absolute window in the ORIGINAL template timeline. The
    # rebuild generator uses it to classify the 3-phase overlay clips
    # (phase-1 ends at tpl_start, phase-3 begins at tpl_end, phase-2 between).
    tpl_start_sec: float = 0.0
    tpl_end_sec: float = 0.0
    # Frozen internal structure captured from the template describe-dump.
    subclips: list[PromoSubclip] = Field(default_factory=list)


class EditPlan(BaseModel):
    game_slug: str
    video_slug: str
    fragments: list[Fragment] = Field(default_factory=list)
    promo: PromoPlan = Field(default_factory=PromoPlan)

    # Computed timeline (seconds).
    gameplay_duration_sec: float = 0.0
    promo_insertion_sec: float = 0.0     # block start on the timeline; 0 if none
    total_duration_sec: float = 0.0
    # The ORIGINAL template's total length (decor/overlay/music span). The
    # rebuild stretches those tracks from tpl_total -> total_duration_sec.
    tpl_total_sec: float = 0.0

    # Echo of the resolved template profile so the generator is self-contained.
    template_profile: dict = Field(default_factory=dict)

    def write(self, path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.model_dump_json(indent=2), encoding="utf-8")
        return path


# --------------------------------------------------------------------------- #
# Silence detection
# --------------------------------------------------------------------------- #
_SIL_START = re.compile(r"silence_start:\s*(-?\d+(?:\.\d+)?)")
_SIL_END = re.compile(r"silence_end:\s*(-?\d+(?:\.\d+)?)")


def _detect_silences(
    media: Path,
    *,
    duration_sec: float,
    threshold_db: float,
    min_silence_sec: float,
) -> list[tuple[float, float]]:
    """Run ffmpeg silencedetect; return [(start, end), ...] silence spans."""
    proc = subprocess.run(
        [
            ffmpeg_bin(),
            "-hide_banner", "-nostats",
            "-i", str(media),
            "-af", f"silencedetect=noise={threshold_db}dB:d={min_silence_sec}",
            "-f", "null", "-",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    silences: list[tuple[float, float]] = []
    cur_start: float | None = None
    for line in proc.stderr.splitlines():  # silencedetect writes to stderr
        ms = _SIL_START.search(line)
        if ms:
            cur_start = max(0.0, float(ms.group(1)))
            continue
        me = _SIL_END.search(line)
        if me and cur_start is not None:
            silences.append((cur_start, min(duration_sec, float(me.group(1)))))
            cur_start = None
    if cur_start is not None:  # trailing silence with no explicit end -> EOF
        silences.append((cur_start, duration_sec))
    return silences


def edges_keep_span(
    silences: list[tuple[float, float]],
    *,
    duration_sec: float,
    keep_margin_sec: float,
    min_keep_sec: float,
) -> tuple[float, float]:
    """Single keep span = clip minus leading/trailing dead air only.

    Pure (no ffmpeg) so it is unit-tested directly.
    """
    keep_in = 0.0
    keep_out = duration_sec
    for s_start, s_end in silences:
        if s_start <= keep_margin_sec:                   # leading dead air
            keep_in = max(keep_in, s_end)
        if s_end >= duration_sec - keep_margin_sec:      # trailing dead air
            keep_out = min(keep_out, s_start)
    a = max(0.0, keep_in - keep_margin_sec)
    b = min(duration_sec, keep_out + keep_margin_sec)
    if b - a < min_keep_sec:                             # safety: whole clip
        a, b = 0.0, duration_sec
    return a, b


def detect_keep_segments(
    media: Path,
    *,
    duration_sec: float,
    threshold_db: float,
    min_silence_sec: float,
    keep_margin_sec: float,
    min_keep_sec: float,
    mode: str = "edges",
) -> list[KeepSegment]:
    """Spans to KEEP.

    mode="edges" (default): drop ONLY leading/trailing dead air; the take's
    natural flow (internal pauses) is preserved -> one keep span per
    fragment. This is what the channel actually does by hand and avoids
    jump-cut soup.

    mode="internal": also cut long internal silences (aggressive).
    """
    silences = _detect_silences(
        media,
        duration_sec=duration_sec,
        threshold_db=threshold_db,
        min_silence_sec=min_silence_sec,
    )

    if mode == "edges":
        a, b = edges_keep_span(
            silences,
            duration_sec=duration_sec,
            keep_margin_sec=keep_margin_sec,
            min_keep_sec=min_keep_sec,
        )
        return [KeepSegment(src_in_sec=round(a, 4), src_out_sec=round(b, 4))]

    # mode == "internal": invert every silence -> many keep spans.
    keeps: list[KeepSegment] = []
    cursor = 0.0
    for s_start, s_end in silences:
        if s_start > cursor:
            keeps.append(KeepSegment(src_in_sec=cursor, src_out_sec=s_start))
        cursor = max(cursor, s_end)
    if cursor < duration_sec:
        keeps.append(KeepSegment(src_in_sec=cursor, src_out_sec=duration_sec))

    out: list[KeepSegment] = []
    for k in keeps:
        a = max(0.0, k.src_in_sec - keep_margin_sec)
        b = min(duration_sec, k.src_out_sec + keep_margin_sec)
        if b - a >= min_keep_sec:
            out.append(KeepSegment(src_in_sec=round(a, 4), src_out_sec=round(b, 4)))
    if not out and duration_sec > 0:
        out.append(KeepSegment(src_in_sec=0.0, src_out_sec=round(duration_sec, 4)))
    return out


# --------------------------------------------------------------------------- #
# Promo block extraction from the template describe-dump
# --------------------------------------------------------------------------- #
def _describe_dump_path(slug: str) -> Path:
    return TMP_DIR / f"{slug}_describe.json"


def extract_promo_block(game: GameConfig) -> PromoPlan:
    """Freeze the promo's rigid internal structure from the describe-dump.

    Reads data/tmp/<slug>_describe.json (produced by describe_project.jsx),
    finds the promo clips on the content video + gameplay audio tracks
    (matched by `promo.clip_name_contains`), and records each piece in
    block-relative time. The block's length is governed by the continuous
    video; the audio's internal gap is preserved implicitly because each
    audio piece keeps its own rel start/end + source in/out.
    """
    pt = game.premiere_template
    promo_cfg = pt.promo
    if not promo_cfg.present:
        return PromoPlan(present=False)

    dump = _describe_dump_path(game.slug)
    if not dump.exists():
        raise FileNotFoundError(
            f"Promo block needs the template describe-dump but {dump} is "
            f"missing. Open {pt.template_filename} in Premiere and run "
            f"scripts/jsx/describe_project.jsx first."
        )
    data = json.loads(dump.read_text(encoding="utf-8"))

    needle = promo_cfg.clip_name_contains.lower()

    def track_by_label(kind_key: str, label: str) -> dict | None:
        for t in data.get(kind_key, []):
            if t.get("label") == label:
                return t
        return None

    vt = track_by_label("video_tracks", pt.content_video_track)
    at = track_by_label("audio_tracks", pt.gameplay_audio_track)
    if vt is None or at is None:
        raise ValueError(
            f"Describe-dump missing {pt.content_video_track}/"
            f"{pt.gameplay_audio_track} for {game.slug}."
        )

    def promo_clips(track: dict) -> list[dict]:
        hits = [
            c for c in track.get("clips", [])
            if needle in str(c.get("name", "")).lower()
        ]
        return sorted(hits, key=lambda c: c.get("start_sec") or 0.0)

    v_clips = promo_clips(vt)
    a_clips = promo_clips(at)
    if not v_clips:
        raise ValueError(
            f"No promo clips matching {promo_cfg.clip_name_contains!r} on "
            f"{pt.content_video_track} in the {game.slug} template."
        )

    # Block origin = earliest promo clip start across both tracks; block end =
    # latest promo VIDEO clip end (video governs the rigid length).
    block_start = min(
        (c["start_sec"] for c in v_clips + a_clips if c.get("start_sec") is not None),
        default=0.0,
    )
    block_end = max(c["end_sec"] for c in v_clips if c.get("end_sec") is not None)
    block_dur = round(block_end - block_start, 4)

    subclips: list[PromoSubclip] = []
    for c in v_clips:
        subclips.append(
            PromoSubclip(
                track_role="content_video",
                rel_start_sec=round(c["start_sec"] - block_start, 4),
                rel_end_sec=round(c["end_sec"] - block_start, 4),
                src_in_sec=round(c.get("inPoint_sec") or 0.0, 4),
                src_out_sec=round(c.get("outPoint_sec") or 0.0, 4),
            )
        )
    for c in a_clips:
        subclips.append(
            PromoSubclip(
                track_role="gameplay_audio",
                rel_start_sec=round(c["start_sec"] - block_start, 4),
                rel_end_sec=round(c["end_sec"] - block_start, 4),
                src_in_sec=round(c.get("inPoint_sec") or 0.0, 4),
                src_out_sec=round(c.get("outPoint_sec") or 0.0, 4),
            )
        )

    from ..paths import aptoide_ads_dir

    asset = aptoide_ads_dir() / promo_cfg.asset_filename if promo_cfg.asset_filename else None
    return PromoPlan(
        present=True,
        asset_path=str(asset) if asset else "",
        block_duration_sec=block_dur,
        tpl_start_sec=round(block_start, 4),
        tpl_end_sec=round(block_end, 4),
        subclips=subclips,
    )


def _template_total_sec(slug: str) -> float:
    """Original template length = the longest clip end in its describe-dump
    (the static decor / music span the whole video).
    """
    dump = _describe_dump_path(slug)
    if not dump.exists():
        return 0.0
    data = json.loads(dump.read_text(encoding="utf-8"))
    ends: list[float] = []
    for t in data.get("video_tracks", []) + data.get("audio_tracks", []):
        for c in t.get("clips", []):
            e = c.get("end_sec")
            if e is not None:
                ends.append(float(e))
    return round(max(ends), 4) if ends else 0.0


# --------------------------------------------------------------------------- #
# Plan builder
# --------------------------------------------------------------------------- #
def list_fragments(folder: Path) -> list[Path]:
    """Recorded fragments in the folder, ordered by filename (timestamp/NNN).

    Paths are resolved to ABSOLUTE — Premiere's importFiles() can't open a
    path relative to its own working directory.
    """
    files = [
        p.resolve()
        for p in folder.iterdir()
        if p.is_file() and p.suffix.lower() in FRAGMENT_EXTS
    ]
    return sorted(files, key=lambda p: p.name)


def _snap_insertion(
    target: float,
    boundaries: list[float],
    total_gameplay: float,
) -> float:
    """Snap the promo target offset to the nearest clean boundary.

    `boundaries` are cumulative gameplay offsets where a cut is 'clean'
    (fragment ends, or post-silence keep-segment ends). Falls back to the
    raw target clamped into (0, total_gameplay).
    """
    if not boundaries:
        return max(0.0, min(target, total_gameplay))
    return min(boundaries, key=lambda b: abs(b - target))


def place_promo_split(
    fragments: list[Fragment],
    silences_by_frag: dict[int, list[tuple[float, float]]],
    *,
    target: float,
    gameplay_dur: float,
    snap: str,
) -> float:
    """Pick the promo insertion offset and (usually) split ONE keep segment.

    Default: find the natural pause (silence) nearest `target` and split the
    keep segment there — so takes stay whole except for the single promo cut.
    snap="exact" splits exactly at target (may cut mid-sentence).
    Returns the gameplay-time offset where the promo block begins.
    """
    target = max(0.0, min(target, gameplay_dur))

    # Flatten keeps to a gameplay timeline: (frag_idx, seg_idx, g0, g1, seg).
    flat: list[tuple[int, int, float, float, KeepSegment]] = []
    g = 0.0
    for fr in fragments:
        for si, seg in enumerate(fr.keep_segments):
            flat.append((fr.index, si, g, round(g + seg.duration_sec, 4), seg))
            g = round(g + seg.duration_sec, 4)

    # Which keep segment spans the target?
    host = next((t for t in flat if t[2] <= target < t[3]), flat[-1] if flat else None)
    if host is None:
        return target
    fi, si, g0, _g1, seg = host
    src_target = seg.src_in_sec + (target - g0)

    split_src: float | None = None
    if snap != "exact":
        cands = [
            (ss + se) / 2.0
            for (ss, se) in silences_by_frag.get(fi, [])
            if seg.src_in_sec < (ss + se) / 2.0 < seg.src_out_sec
        ]
        if cands:
            split_src = min(cands, key=lambda c: abs(c - src_target))
    if split_src is None:
        split_src = min(max(src_target, seg.src_in_sec), seg.src_out_sec)

    # Split the host keep segment in place (unless the split is at an edge).
    fr = fragments[fi]
    if seg.src_in_sec + 1e-3 < split_src < seg.src_out_sec - 1e-3:
        fr.keep_segments[si : si + 1] = [
            KeepSegment(src_in_sec=round(seg.src_in_sec, 4), src_out_sec=round(split_src, 4)),
            KeepSegment(src_in_sec=round(split_src, 4), src_out_sec=round(seg.src_out_sec, 4)),
        ]
    return round(g0 + (split_src - seg.src_in_sec), 4)


def build_edit_plan(
    game: GameConfig,
    video_slug: str,
    fragments_dir: Path,
    *,
    snap_boundaries: str | None = None,
) -> EditPlan:
    """Probe + silence-trim fragments and compute the timeline."""
    pt = game.premiere_template
    sil = pt.silence
    snap = snap_boundaries or pt.promo.snap

    frag_paths = list_fragments(fragments_dir)
    if not frag_paths:
        raise FileNotFoundError(f"No recorded fragments found in {fragments_dir}")

    fragments: list[Fragment] = []
    silences_by_frag: dict[int, list[tuple[float, float]]] = {}
    running = 0.0
    for i, fp in enumerate(frag_paths):
        dur = probe_duration_sec(fp)
        silences = _detect_silences(
            fp,
            duration_sec=dur,
            threshold_db=sil.threshold_db,
            min_silence_sec=sil.min_silence_sec,
        )
        silences_by_frag[i] = silences
        if sil.mode == "edges":
            a, b = edges_keep_span(
                silences,
                duration_sec=dur,
                keep_margin_sec=sil.keep_margin_sec,
                min_keep_sec=sil.min_keep_sec,
            )
            keeps = [KeepSegment(src_in_sec=round(a, 4), src_out_sec=round(b, 4))]
        else:
            keeps = detect_keep_segments(
                fp,
                duration_sec=dur,
                threshold_db=sil.threshold_db,
                min_silence_sec=sil.min_silence_sec,
                keep_margin_sec=sil.keep_margin_sec,
                min_keep_sec=sil.min_keep_sec,
                mode=sil.mode,
            )
        for k in keeps:
            running = round(running + k.duration_sec, 4)
        fragments.append(
            Fragment(
                index=i,
                path=str(fp),
                probe_duration_sec=round(dur, 4),
                keep_segments=keeps,
            )
        )

    gameplay_dur = running

    promo = extract_promo_block(game)
    if promo.present:
        insertion = place_promo_split(
            fragments,
            silences_by_frag,
            target=pt.promo.target_offset_sec,
            gameplay_dur=gameplay_dur,
            snap=snap,
        )
        total = round(gameplay_dur + promo.block_duration_sec, 4)
    else:
        insertion = 0.0
        total = gameplay_dur

    return EditPlan(
        game_slug=game.slug,
        video_slug=video_slug,
        fragments=fragments,
        promo=promo,
        gameplay_duration_sec=gameplay_dur,
        promo_insertion_sec=round(insertion, 4),
        total_duration_sec=total,
        tpl_total_sec=_template_total_sec(game.slug),
        template_profile=pt.model_dump(mode="json"),
    )
