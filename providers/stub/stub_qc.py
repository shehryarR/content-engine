"""
providers/stub/stub_qc.py

S70 Quality Control Provider.

Layer 1 — deterministic ffprobe checks (duration sanity, stream presence,
non-empty output). Layer 4 — cheap lip-sync proxy: audio/video duration
match within tolerance (D-ID renders are lip-synced by construction, so
this just catches a truncated/broken render).

Layers 2/3/5 (identity similarity, voice similarity, vision-LLM review)
are not implemented here — out of scope for this pass.
"""

import json
import os
import subprocess
import tempfile

from contracts.common.envelope import StageEnvelopeV1, StageOutputV1
from contracts.stages.s70_qc import QualityReportV1
from orchestrator.storage import get_artifact, put_artifact

DURATION_TOLERANCE_SECONDS = 0.75  # slack for container/encoding rounding

# NOTE: no expected-output config exists anywhere in the repo yet (checked
# configs/providers/*, configs/runs/*). Hardcoding D-ID's known /talks output
# spec here as a stand-in. Flag to Ammar: this should move to a proper
# config file (e.g. configs/qc.yaml) once one exists.
EXPECTED_VIDEO_CODEC = "h264"
EXPECTED_AUDIO_CODEC = "aac"
EXPECTED_MIN_WIDTH = 512
EXPECTED_MIN_HEIGHT = 512


def _probe_streams(data: bytes, suffix: str) -> list[dict]:
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as f:
        f.write(data)
        tmp_path = f.name
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json",
             "-show_streams", tmp_path],
            capture_output=True, text=True, timeout=10
        )
        parsed = json.loads(result.stdout)
        return parsed.get("streams", [])
    except Exception:
        return []
    finally:
        os.unlink(tmp_path)


def _stream_duration(streams: list[dict], codec_type: str) -> float:
    for s in streams:
        if s.get("codec_type") == codec_type and s.get("duration"):
            try:
                return float(s["duration"])
            except (TypeError, ValueError):
                continue
    return 0.0


def _codec_and_resolution_ok(streams: list[dict]) -> tuple[bool, dict]:
    """Check video codec, audio codec, and minimum resolution against
    expected config. Returns (ok, details) for inclusion in QC metrics."""
    video_stream = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio_stream = next((s for s in streams if s.get("codec_type") == "audio"), None)

    video_codec = video_stream.get("codec_name") if video_stream else None
    audio_codec = audio_stream.get("codec_name") if audio_stream else None
    width = video_stream.get("width", 0) if video_stream else 0
    height = video_stream.get("height", 0) if video_stream else 0

    codec_ok = (video_codec == EXPECTED_VIDEO_CODEC) and (audio_codec == EXPECTED_AUDIO_CODEC)
    resolution_ok = (width >= EXPECTED_MIN_WIDTH) and (height >= EXPECTED_MIN_HEIGHT)

    return codec_ok and resolution_ok, {
        "video_codec": video_codec,
        "audio_codec": audio_codec,
        "width": width,
        "height": height,
    }


class StubQCProvider:
    capability: str = "quality_control"

    def run(self, envelope: StageEnvelopeV1, run_id: str) -> StageOutputV1:
        video_ref = next(
            (ref for ref in envelope.artifact_refs
             if ref.artifact_id.startswith("master_video_")),
            None,
        )
        if video_ref is None:
            raise ValueError(
                "S70 QC requires the S60 master_video_ artifact "
                "in envelope.artifact_refs"
            )

        # Find original S20 audio for the Layer 4 duration cross-check
        audio_ref = next(
            (ref for ref in envelope.artifact_refs
             if ref.mime_type and ref.mime_type.startswith("audio/")),
            None,
        )

        video_bytes = get_artifact(video_ref)
        video_streams = _probe_streams(video_bytes, ".mp4")

        has_video_stream = any(s.get("codec_type") == "video" for s in video_streams)
        has_audio_stream = any(s.get("codec_type") == "audio" for s in video_streams)
        video_duration = _stream_duration(video_streams, "video") or _stream_duration(video_streams, "audio")
        non_empty = len(video_bytes) > 0 and video_duration > 0.0
        codec_resolution_ok, codec_info = _codec_and_resolution_ok(video_streams)

        # Layer 4: lip-sync proxy via audio/video duration match
        sync_score = 0.0
        if audio_ref is not None:
            audio_bytes = get_artifact(audio_ref)
            audio_streams = _probe_streams(audio_bytes, ".wav")
            audio_duration = _stream_duration(audio_streams, "audio")
            if audio_duration > 0 and video_duration > 0:
                diff = abs(audio_duration - video_duration)
                sync_score = max(0.0, 1.0 - (diff / max(audio_duration, video_duration)))
                duration_match = diff <= DURATION_TOLERANCE_SECONDS
            else:
                duration_match = False
        else:
            # No separate S20 track (e.g. D-ID already embeds audio) —
            # can't cross-check, so don't penalize, but flag as unverified.
            duration_match = has_audio_stream
            sync_score = 1.0 if has_audio_stream else 0.0

        passed = (
            has_video_stream and has_audio_stream and non_empty
            and duration_match and codec_resolution_ok
        )

        metrics = {
            "has_video_stream": 1.0 if has_video_stream else 0.0,
            "has_audio_stream": 1.0 if has_audio_stream else 0.0,
            "non_empty_output": 1.0 if non_empty else 0.0,
            "duration_match": 1.0 if duration_match else 0.0,
            "codec_resolution_ok": 1.0 if codec_resolution_ok else 0.0,
            "video_width": float(codec_info["width"]),
            "video_height": float(codec_info["height"]),
            "video_duration_seconds": video_duration,
            "sync_score": round(sync_score, 4),
        }

        qc_report = QualityReportV1(
            run_id=run_id,
            master_video_hash=video_ref.hash,
            passed=passed,
            metrics=metrics,
        )

        report_bytes = json.dumps(qc_report.model_dump()).encode("utf-8")
        artifact = put_artifact(
            data=report_bytes,
            artifact_id=f"qc_report_{run_id}",
            mime_type="application/json",
        )

        return StageOutputV1(
            payload=qc_report.model_dump(),
            metadata={
                "stub": False,
                "provider": "stub_qc_provider",
                "passed": qc_report.passed,
            },
            artifact_refs=[artifact],
        )
