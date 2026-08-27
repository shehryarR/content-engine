"""
providers/real/narration_conform.py

M4 Day 3: S40 media_sync provider for faceless. Takes S20 narration audio
+ S30 faceless video, conforms durations via FFmpeg, muxes into one MP4.
Same S40 contract as stub_sync.py. No model, no API, no GPU.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path

from contracts.common.envelope import StageEnvelopeV1, StageOutputV1
from contracts.stages.s40_sync import SynchronizedMediaV1
from orchestrator.storage import get_artifact, put_artifact
from providers.base import StageProvider


def _find_audio_ref(envelope: StageEnvelopeV1):
    return next(
        (r for r in envelope.artifact_refs
         if r.mime_type and r.mime_type.startswith("audio/")),
        None,
    )


def _find_video_ref(envelope: StageEnvelopeV1):
    return next(
        (r for r in envelope.artifact_refs
         if r.mime_type and r.mime_type.startswith("video/")),
        None,
    )


def _probe_duration(data: bytes, suffix: str) -> float:
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as f:
        f.write(data)
        tmp = f.name
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json",
             "-show_format", tmp],
            capture_output=True, text=True, timeout=15,
        )
        data_parsed = json.loads(result.stdout)
        return float(data_parsed.get("format", {}).get("duration", 0.0))
    except Exception:
        return 0.0
    finally:
        os.unlink(tmp)


def _conform_and_mux(
    audio_bytes: bytes, video_bytes: bytes, target_duration: float
) -> bytes:
    with tempfile.TemporaryDirectory() as tmp:
        audio_path = os.path.join(tmp, "audio.wav")
        video_path = os.path.join(tmp, "video.mp4")
        output_path = os.path.join(tmp, "synced.mp4")

        with open(audio_path, "wb") as f:
            f.write(audio_bytes)
        with open(video_path, "wb") as f:
            f.write(video_bytes)

        video_duration = _probe_duration(video_bytes, ".mp4")
        diff = target_duration - video_duration

        if abs(diff) <= 0.1:
            cmd = [
                "ffmpeg", "-y",
                "-i", video_path, "-i", audio_path,
                "-c:v", "copy", "-c:a", "aac",
                "-map", "0:v:0", "-map", "1:a:0",
                "-shortest",
                "-map_metadata", "-1", "-fflags", "+bitexact",
                str(output_path),
            ]
        elif diff > 0:
            pad_duration = diff + 0.5
            cmd = [
                "ffmpeg", "-y",
                "-i", video_path, "-i", audio_path,
                "-vf", f"tpad=stop_mode=clone:stop_duration={pad_duration:.2f}",
                "-c:v", "libx264", "-threads", "1", "-pix_fmt", "yuv420p",
                "-c:a", "aac",
                "-map", "0:v:0", "-map", "1:a:0",
                "-shortest",
                "-map_metadata", "-1", "-fflags", "+bitexact",
                "-flags:v", "+bitexact",
                str(output_path),
            ]
        else:
            cmd = [
                "ffmpeg", "-y",
                "-i", video_path, "-i", audio_path,
                "-t", f"{target_duration:.3f}",
                "-c:v", "libx264", "-threads", "1", "-pix_fmt", "yuv420p",
                "-c:a", "aac",
                "-map", "0:v:0", "-map", "1:a:0",
                "-map_metadata", "-1", "-fflags", "+bitexact",
                "-flags:v", "+bitexact",
                str(output_path),
            ]

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if result.returncode != 0 or not os.path.exists(output_path):
            raise RuntimeError(
                f"narration_conform ffmpeg failed: {result.stderr[-2000:]}"
            )

        return Path(output_path).read_bytes()


class NarrationConformProvider(StageProvider):

    @property
    def capability(self) -> str:
        return "media_sync"

    def run(self, envelope: StageEnvelopeV1, run_id: str) -> StageOutputV1:
        audio_ref = _find_audio_ref(envelope)
        if audio_ref is None:
            raise ValueError(
                "narration_conform requires S20 audio artifact in envelope"
            )

        video_ref = _find_video_ref(envelope)
        if video_ref is None:
            raise ValueError(
                "narration_conform requires S30 video artifact in envelope"
            )

        audio_bytes = get_artifact(audio_ref)
        video_bytes = get_artifact(video_ref)

        audio_duration = _probe_duration(audio_bytes, ".wav")
        if audio_duration <= 0:
            raise ValueError(
                "narration_conform: could not measure S20 audio duration"
            )

        synced_bytes = _conform_and_mux(audio_bytes, video_bytes, audio_duration)

        sync_artifact = put_artifact(
            data=synced_bytes,
            artifact_id=f"sync_{run_id}",
            mime_type="video/mp4",
        )

        sync_media = SynchronizedMediaV1(
            run_id=run_id,
            media_artifact=sync_artifact,
        )

        video_duration = _probe_duration(video_bytes, ".mp4")
        output_duration = _probe_duration(synced_bytes, ".mp4")

        return StageOutputV1(
            payload=sync_media.model_dump(),
            metadata={
                "provider": "narration_conform",
                "model": "ffmpeg_mux",
                "version": "1.0.0",
                "stub": False,
                "audio_duration_seconds": audio_duration,
                "video_input_duration_seconds": video_duration,
                "output_duration_seconds": output_duration,
                "conform_action": (
                    "pad" if video_duration < audio_duration - 0.1
                    else "trim" if video_duration > audio_duration + 0.1
                    else "mux_only"
                ),
            },
            artifact_refs=[sync_artifact],
        )