"""
providers/stub_assembly.py
S60 Assembly Provider.
Fetches real S20 audio and S40 video artifacts from the envelope,
muxes them via ffmpeg into a single output file, measures duration
via ffprobe, and stores the result as the master video artifact.
Falls back to the black_5s.mp4 fixture if real artifacts are missing
or ffmpeg is unavailable.
"""
import json
import os
import subprocess
import tempfile
from pathlib import Path

from contracts.common.envelope import StageEnvelopeV1, StageOutputV1
from contracts.stages.idea_request import Modality
from contracts.stages.s60_assembly import MasterVideoV1
from orchestrator.storage import get_artifact, put_artifact

FIXTURE_VIDEO = Path("fixtures") / "stubs" / "black_5s.mp4"


def _measure_duration(video_bytes: bytes) -> float:
    with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as f:
        f.write(video_bytes)
        tmp_path = f.name
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json",
             "-show_streams", tmp_path],
            capture_output=True, text=True, timeout=10
        )
        data = json.loads(result.stdout)
        duration = float(data["streams"][0].get("duration", 5.0))
        return duration
    except Exception:
        return 5.0
    finally:
        os.unlink(tmp_path)


def _has_audio_stream(video_bytes: bytes) -> bool:
    """Return True if the video bytes already contain an audio stream."""
    with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as f:
        f.write(video_bytes)
        tmp_path = f.name
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json",
             "-show_streams", tmp_path],
            capture_output=True, text=True, timeout=10
        )
        data = json.loads(result.stdout)
        return any(s.get("codec_type") == "audio" for s in data.get("streams", []))
    except Exception:
        return False
    finally:
        os.unlink(tmp_path)


def _mux(audio_bytes: bytes, video_bytes: bytes) -> bytes:
    """Mux audio + video into one mp4 via ffmpeg. Returns muxed bytes."""
    with tempfile.TemporaryDirectory() as tmp:
        audio_path = os.path.join(tmp, "audio.wav")
        video_path = os.path.join(tmp, "video.mp4")
        out_path = os.path.join(tmp, "output.mp4")

        with open(audio_path, "wb") as f:
            f.write(audio_bytes)
        with open(video_path, "wb") as f:
            f.write(video_bytes)

        result = subprocess.run(
            [
                "ffmpeg", "-y",
                "-i", video_path,
                "-i", audio_path,
                "-c:v", "copy",
                "-c:a", "aac",
                "-shortest",
                out_path,
            ],
            capture_output=True, timeout=60
        )
        if result.returncode != 0:
            raise RuntimeError(f"ffmpeg failed: {result.stderr.decode()}")

        with open(out_path, "rb") as f:
            return f.read()


class StubAssemblyProvider:
    """S60 assembly — real ffmpeg mux when S20/S40 artifacts present, fixture fallback otherwise."""
    capability: str = "assembly"

    def run(self, envelope: StageEnvelopeV1, run_id: str) -> StageOutputV1:
        # Find audio (S20) and video (S40) artifacts
        audio_ref = None
        video_ref = None
        for ref in envelope.artifact_refs:
            if ref.mime_type and ref.mime_type.startswith("audio/") and audio_ref is None:
                audio_ref = ref
            if ref.mime_type and ref.mime_type.startswith("video/") and video_ref is None:
                video_ref = ref

        stub = False
        try:
            if video_ref is None:
                raise ValueError("Missing S40 video artifact — falling back to fixture")
            video_bytes = get_artifact(video_ref)
            # D-ID already muxes audio into the video — skip mux if audio stream present.
            # Only mux if the video has no audio track (e.g. stub/local render path).
            if _has_audio_stream(video_bytes):
                pass  # D-ID path: audio already embedded, no mux needed
            else:
                if audio_ref is None:
                    raise ValueError("Video has no audio and no S20 audio artifact found")
                audio_bytes = get_artifact(audio_ref)
                video_bytes = _mux(audio_bytes, video_bytes)
        except Exception as e:
            print(f"[assembly] falling back to fixture: {e}")
            video_bytes = FIXTURE_VIDEO.read_bytes()
            stub = True

        artifact = put_artifact(
            data=video_bytes,
            artifact_id=f"master_video_{run_id}",
            mime_type="video/mp4",
        )

        duration = _measure_duration(video_bytes)

        master_video = MasterVideoV1(
            run_id=run_id,
            modality=Modality.AVATAR,
            video_artifact=artifact,
            scene_count=3,
            duration_seconds=duration,
        )

        return StageOutputV1(
            payload=master_video.model_dump(mode="json"),
            metadata={
                "stub": stub,
                "provider": "stub_assembly_provider",
                "scene_count": master_video.scene_count,
                "duration_seconds": master_video.duration_seconds,
            },
            artifact_refs=[artifact],
        )
