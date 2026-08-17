from __future__ import annotations


import hashlib
from datetime import datetime, timezone
from pathlib import Path


from contracts.common.envelope import  StageEnvelopeV1, StageOutputV1
from contracts.stages.s40_sync import SynchronizedMediaV1
from orchestrator.storage import put_artifact,get_artifact  
from providers.base import StageProvider


class StubSyncProvider(StageProvider):
    """Stub sync provider that passes through the video artifact from S30."""

    @property
    def capability(self) -> str:
        return "media_sync"

    def run(self, envelope: StageEnvelopeV1, run_id: str) -> StageOutputV1:
        video_ref = next(
            (ref for ref in envelope.artifact_refs if ref.mime_type and ref.mime_type.startswith("video/")),
            None,
        )
        if video_ref is None:
            raise FileNotFoundError("S40 sync stub requires a video artifact from S30")

        video_bytes = get_artifact(video_ref)
        sync_artifact = put_artifact(
            data=video_bytes,
            artifact_id=f"sync_{run_id}",
            mime_type="video/mp4",
        )

        sync_media = SynchronizedMediaV1(run_id=run_id, media_artifact=sync_artifact)

        return StageOutputV1(
            payload=sync_media.model_dump(),
            metadata={"provider": "stub_sync", "model": "stub_v1", "version": "1.0.0", "stub": True, "pass_through": True},
            artifact_refs=[sync_artifact],
        )

# --- S40 exit validator logic. Wrapped as a StageValidator and registered
# in orchestrator/stage_executor.py (STAGE_VALIDATORS["S40"]). ---
#
# This is deliberately narrower than S30's validator: it only checks the
# thing that's specific to "sync" - that S40's output duration actually
# matches S20's audio duration - not general video validity (S30's
# validator already covers "is this a real video", and in stub mode S40
# is re-storing S30's exact bytes anyway). This is the check that would
# have caught the old byte-for-byte pass-through bug immediately, had it
# existed when that bug was live.

import json
import os
import subprocess
import tempfile

SYNC_DURATION_TOLERANCE_SECONDS = 2.0


def _probe_video_duration(video_bytes: bytes) -> float:
    with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as f:
        f.write(video_bytes)
        tmp_path = f.name
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_streams", tmp_path],
            capture_output=True, text=True, timeout=10,
        )
        data = json.loads(result.stdout)
        for s in data.get("streams", []):
            if s.get("codec_type") == "video" and s.get("duration"):
                try:
                    return float(s["duration"])
                except (TypeError, ValueError):
                    continue
        return 0.0
    except Exception:
        return 0.0
    finally:
        os.unlink(tmp_path)


def validate_sync(
    video_bytes: bytes,
    expected_audio_duration: float | None = None,
    tolerance: float = SYNC_DURATION_TOLERANCE_SECONDS,
) -> tuple[bool, list[str]]:
    """
    Checks: S40's output duration falls within tolerance of S20's audio
    duration. Returns (passed, failures). Does not re-check general video
    validity - that's S30's validator's job.
    """
    if not video_bytes:
        return False, ["S40 sync output is empty"]

    duration = _probe_video_duration(video_bytes)
    if duration <= 0:
        return False, ["S40 sync output has zero/invalid duration"]

    if expected_audio_duration is None or expected_audio_duration <= 0:
        # Nothing to cross-check against - not a failure, just unverifiable.
        return True, []

    diff = abs(duration - expected_audio_duration)
    if diff > tolerance:
        return False, [
            f"S40 sync output duration {duration:.2f}s differs from expected "
            f"audio duration {expected_audio_duration:.2f}s by {diff:.2f}s "
            f"(tolerance {tolerance}s)"
        ]
    return True, []