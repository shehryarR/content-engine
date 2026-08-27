"""
providers/real/faceless_mixed_media.py

M4 Day 2 (Owner C / Hanab): S30 avatar_render provider for the faceless
modality. Produces a deterministic placeholder video from the S10 script's
per-scene text - no face, no identity, no external API - satisfying the
same VisualRequestV1 -> PrimaryVisualTrackV1 contract did_avatar.py and
stub_avatar.py satisfy for the avatar path.

Explicitly does NOT: read identity_id, call any face API, import
did_avatar.py, or touch contracts/ or graph/.

Per-scene duration is derived from the real S20 audio artifact's measured
duration (split evenly across scenes) when present, so Step 1 of
_validate_avatar_render_stage's structural check (video/audio duration
tolerance) isn't fighting a hardcoded video length against a real
narration track. Falls back to SCENE_DURATION_SECONDS per scene only if
no audio artifact is found in the envelope (e.g. isolated S30 testing).
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path

from contracts.common.envelope import StageEnvelopeV1, StageOutputV1
from contracts.stages.s10_script import ScriptPackageV1
from contracts.stages.s30_avatar import PrimaryVisualTrackV1
from orchestrator.storage import get_artifact, put_artifact
from providers.base import StageProvider

SCENE_DURATION_SECONDS = 5.0  # fallback only, used when no audio ref present
FRAME_SIZE = "1920x1080"
BACKGROUND_COLOR = "0x2a2a2a"

# Override with FACELESS_FONT_PATH if this default isn't present on the
# machine running the pipeline - fonts-dejavu-core provides it on Debian/
# Ubuntu. Checked explicitly (rather than letting ffmpeg's drawtext fail
# with an opaque error) so a missing font is a clear message, not a
# silent broken video.
_DEFAULT_FONT_PATH = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"


def _find_script_ref(envelope: StageEnvelopeV1):
    """The S10 script artifact, threaded into S30's envelope the same way
    identity_ref and the S20 audio ref are - matched by the artifact_id
    convention stub_script.py/openai_script.py both use: f"script_{run_id}"."""
    return next(
        (
            ref for ref in envelope.artifact_refs
            if ref.mime_type == "application/json"
            and ref.artifact_id.startswith("script_")
        ),
        None,
    )


def _find_audio_ref(envelope: StageEnvelopeV1):
    """Same lookup stage_executor.py's _get_upstream_audio_duration uses."""
    return next(
        (r for r in envelope.artifact_refs
         if r.mime_type and r.mime_type.startswith("audio/")),
        None,
    )


def _measure_audio_duration(audio_bytes: bytes) -> float:
    with tempfile.NamedTemporaryFile(suffix=".bin", delete=False) as f:
        f.write(audio_bytes)
        tmp_path = f.name
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", tmp_path],
            capture_output=True, text=True, timeout=15,
        )
        data = json.loads(result.stdout)
        return float(data.get("format", {}).get("duration", 0.0))
    except Exception:
        return 0.0
    finally:
        os.unlink(tmp_path)


def _resolve_font_path() -> str:
    font_path = os.environ.get("FACELESS_FONT_PATH", _DEFAULT_FONT_PATH)
    if not Path(font_path).exists():
        raise FileNotFoundError(
            f"No font found at {font_path}. Install fonts-dejavu-core "
            f"(sudo apt install fonts-dejavu-core) or set FACELESS_FONT_PATH "
            f"to an existing .ttf file."
        )
    return font_path


def _render_scene_segment(
    scene_text: str, index: int, duration: float, font_path: str, workdir: Path
) -> Path:
    """Deterministic solid-colour title card for one scene, via ffmpeg's
    lavfi color source + drawtext. -threads 1 and -map_metadata -1 are
    both there for reproducibility: multi-threaded x264 and embedded
    creation_time metadata are the two most likely sources of a
    same-input-different-bytes result on rerun."""
    textfile_path = workdir / f"scene_{index}.txt"
    textfile_path.write_text(scene_text, encoding="utf-8")

    segment_path = workdir / f"scene_{index}.mp4"
    cmd = [
        "ffmpeg", "-y",
        "-f", "lavfi",
        "-i", f"color=c={BACKGROUND_COLOR}:s={FRAME_SIZE}:d={duration}:r=30",
        "-vf",
        (
            f"drawtext=fontfile={font_path}:textfile={textfile_path}:"
            "fontcolor=white:fontsize=42:line_spacing=12:"
            "x=(w-text_w)/2:y=(h-text_h)/2:"
            "box=1:boxcolor=black@0.4:boxborderw=20"
        ),
        "-map_metadata", "-1",
        "-fflags", "+bitexact",
        "-flags:v", "+bitexact",
        "-c:v", "libx264",
        "-threads", "1",
        "-pix_fmt", "yuv420p",
        "-t", str(duration),
        str(segment_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    if result.returncode != 0 or not segment_path.exists():
        raise RuntimeError(f"ffmpeg failed rendering scene {index}: {result.stderr[-2000:]}")
    return segment_path


def _concatenate_segments(segment_paths: list[Path], workdir: Path) -> bytes:
    list_path = workdir / "concat_list.txt"
    list_path.write_text(
        "\n".join(f"file '{p.resolve()}'" for p in segment_paths),
        encoding="utf-8",
    )
    output_path = workdir / "concatenated.mp4"
    cmd = [
        "ffmpeg", "-y",
        "-f", "concat", "-safe", "0",
        "-i", str(list_path),
        "-map_metadata", "-1",
        "-c", "copy",
        str(output_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    if result.returncode != 0 or not output_path.exists():
        raise RuntimeError(f"ffmpeg concat failed: {result.stderr[-2000:]}")
    return output_path.read_bytes()


class FacelessMixedMediaProvider(StageProvider):
    """S30 avatar_render provider for faceless runs. See module docstring."""

    @property
    def capability(self) -> str:
        return "avatar_render"

    def run(self, envelope: StageEnvelopeV1, run_id: str) -> StageOutputV1:
        script_ref = _find_script_ref(envelope)
        if script_ref is None:
            raise ValueError(
                "faceless_mixed_media requires the S10 script artifact "
                "(artifact_id starting with 'script_') in envelope.artifact_refs "
                "- none found."
            )
        script_bytes = get_artifact(script_ref)
        script = ScriptPackageV1.model_validate(json.loads(script_bytes))
        scene_count = len(script.scenes)

        audio_ref = _find_audio_ref(envelope)
        if audio_ref is not None:
            audio_duration = _measure_audio_duration(get_artifact(audio_ref))
            scene_duration = (
                audio_duration / scene_count if audio_duration > 0 else SCENE_DURATION_SECONDS
            )
        else:
            scene_duration = SCENE_DURATION_SECONDS

        font_path = _resolve_font_path()

        with tempfile.TemporaryDirectory() as tmp:
            workdir = Path(tmp)
            segment_paths = [
                _render_scene_segment(scene_text, i, scene_duration, font_path, workdir)
                for i, scene_text in enumerate(script.scenes)
            ]
            video_bytes = _concatenate_segments(segment_paths, workdir)

        artifact_ref = put_artifact(
            data=video_bytes,
            artifact_id=f"avatar_{run_id}",
            mime_type="video/mp4",
        )

        visual_track = PrimaryVisualTrackV1(run_id=run_id, video_artifact=artifact_ref)

        return StageOutputV1(
            payload=visual_track.model_dump(),
            metadata={
                "provider": "faceless_mixed_media",
                "model": "ffmpeg_lavfi_titlecard",
                "version": "1.0.0",
                "scene_count": scene_count,
                "scene_duration_seconds": scene_duration,
                "duration_seconds": scene_duration * scene_count,
                "stub": False,
            },
            artifact_refs=[artifact_ref],
        )
