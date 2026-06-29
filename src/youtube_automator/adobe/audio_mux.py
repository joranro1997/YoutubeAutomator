"""Post-render audio mux — splice the gameplay voice into the exported mp4.

Why this exists: in the user's Premiere version, every CLONED audio cluster
(no matter how we sanitize the XML — fresh ObjectID/ObjectUID/FileKey/
DefMappingID/ClipID/content-state GUIDs cleared) keeps playing the BLUEPRINT
recording's audio at render time. Video clones repath fine; only audio
doesn't. So we don't ask Premiere to play the gameplay voice at all — we
mux it in after AME exports the mp4.

The rebuild emits ``<slug>_gameplay_audio.wav`` next to the .prproj (a
continuous concatenation of the trimmed keep-segments). This module:

  1. Builds a "positioned voice" stream: voice[0:at_cut] + silence(block) +
     voice[at_cut:gameplay_dur] — exactly aligned to the master timeline.
  2. amixes it on top of the rendered mp4's audio (music + promo) with
     normalize=0 (straight sum, no auto-attenuation).
  3. Re-encodes audio (AAC 192k) and stream-copies video; atomically
     replaces the original mp4.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from .edit_plan import EditPlan, ffmpeg_bin


def mux_gameplay_audio(
    mp4_path: Path,
    gameplay_wav: Path,
    plan: EditPlan,
    promo_wav: Path | None = None,
) -> Path:
    """Splice the gameplay voice (and, when present, the promo audio) into
    ``mp4_path`` (in place) using ffmpeg.

    The rendered mp4 carries music only on the promo block — both the gameplay
    voice and the pre-recorded promo audio are muxed here (the .prproj can't
    render either without de-duping the clone to the first gameplay recording).
    ``promo_wav`` is positioned exactly at the promo block; the gameplay voice
    is split around it. Returns ``mp4_path``; raises CalledProcessError on ffmpeg failure.
    """
    if not mp4_path.exists():
        raise FileNotFoundError(f"rendered mp4 not found: {mp4_path}")
    if not gameplay_wav.exists():
        raise FileNotFoundError(f"gameplay audio WAV not found: {gameplay_wav}")

    at_cut = plan.promo_insertion_sec
    block = plan.promo.block_duration_sec
    gdur = plan.gameplay_duration_sec
    has_promo = bool(plan.promo.present and block > 0 and 0 < at_cut < gdur)

    if has_promo:
        voice_filter = (
            f"[1:a]atrim=0:{at_cut},asetpts=PTS-STARTPTS[va];"
            f"aevalsrc=0:d={block}:s=48000:c=stereo[vs];"
            f"[1:a]atrim={at_cut}:{gdur},asetpts=PTS-STARTPTS[vb];"
            f"[va][vs][vb]concat=n=3:v=0:a=1[voice];"
        )
    else:
        voice_filter = "[1:a]aresample=48000,asetpts=PTS-STARTPTS[voice];"

    inputs = ["-i", str(mp4_path), "-i", str(gameplay_wav)]
    promo_filter = ""
    amix_labels = "[0:a][voice]"
    amix_n = 2
    # Promo audio: delay it to the promo insertion so it lands in the gap the
    # gameplay voice leaves open. (Input 2.)
    if has_promo and promo_wav is not None and promo_wav.exists():
        inputs += ["-i", str(promo_wav)]
        promo_filter = f"[2:a]adelay={int(round(at_cut * 1000))}:all=1[promo];"
        amix_labels = "[0:a][voice][promo]"
        amix_n = 3

    # normalize=0 -> straight sum (the rendered audio already carries music
    # at the user's chosen level; voice + promo ride on top at full).
    filt = (
        voice_filter + promo_filter
        + f"{amix_labels}amix=inputs={amix_n}:duration=longest:normalize=0[a]"
    )

    tmp_out = mp4_path.with_name(mp4_path.stem + ".mux.tmp.mp4")
    cmd = [
        ffmpeg_bin(), "-y",
        *inputs,
        "-filter_complex", filt,
        "-map", "0:v:0", "-map", "[a]",
        "-c:v", "copy",
        "-c:a", "aac", "-b:a", "192k",
        "-movflags", "+faststart",
        str(tmp_out),
    ]
    subprocess.run(cmd, check=True, capture_output=True)

    # Atomically replace.
    mp4_path.unlink()
    tmp_out.rename(mp4_path)
    return mp4_path
