from _future_ import annotations

import json
import time

import requests

from contracts.common.envelope import (
    ArtifactRefV1,
    StageEnvelopeV1,
    StageOutputV1,
)
from contracts.stages.s30_avatar import PrimaryVisualTrackV1
from orchestrator.provider_config import load_provider_config
from orchestrator.storage import get_artifact, put_artifact
from providers.base import StageProvider
import base64


def _raise_with_body(response: requests.Response) -> None:
    """Raise with the response body attached, so a D-ID failure shows the
    actual error detail (e.g. quota exceeded, bad source_url, malformed
    audio) instead of just a status code. Bodies are usually JSON, but
    fall back to raw text if D-ID ever returns something else."""
    if response.status_code >= 400:
        try:
            detail = response.json()
        except ValueError:
            detail = response.text
        raise requests.exceptions.HTTPError(
            f"{response.status_code} error from {response.url}: {detail}",
            response=response,
        )


class DIDAvatarProvider(StageProvider):
    """
    D-ID provider for S30 avatar rendering.

    The provider:
    1. Reads the identity image and S20 audio from the stage envelope.
    2. Uploads both files to D-ID temporary storage.
    3. Creates a D-ID /talks job.
    4. Polls until the video is ready.
    5. Downloads the generated MP4.
    6. Stores the MP4 in MinIO and returns its artifact reference.
    """

    capability: str = "avatar_render"

    def _init_(self):
        config = load_provider_config("avatar_render")

        api_key = config.get("api_key")
        if not api_key:
            raise ValueError(
                "D-ID API key is missing from avatar_render provider config."
            )

        self._api_key = api_key
        self._base_url = config.get(
            "base_url",
            "https://api.d-id.com",
        ).rstrip("/")

        self._poll_interval = int(
            config.get("poll_interval_seconds", 5)
        )
        self._poll_timeout = int(
            config.get("poll_timeout_seconds", 300)
        )

        self._headers = {
            "Authorization": f"Basic {base64.b64encode(self._api_key.encode()).decode()}",
        }

    def _upload_image(self, image_data: bytes, mime_type: str) -> str:
        """Upload the identity image to D-ID temporary storage."""

        extension = "jpg" if mime_type == "image/jpeg" else "png"

        response = requests.post(
            f"{self._base_url}/images",
            headers=self._headers,
            files={
                "image": (
                    f"identity.{extension}",
                    image_data,
                    mime_type,
                )
            },
            timeout=180,
        )

        _raise_with_body(response)

        data = response.json()
        image_url = data.get("url")

        if not image_url:
            raise ValueError(
                f"D-ID image upload returned no URL: {data}"
            )

        return image_url

    def _upload_audio(
        self,
        audio_data: bytes,
        mime_type: str,
    ) -> str:
        """Upload the S20 audio to D-ID temporary storage."""

        extension = {
            "audio/mpeg": "mp3",
            "audio/mp3": "mp3",
            "audio/wav": "wav",
            "audio/x-wav": "wav",
        }.get(mime_type, "mp3")

        response = requests.post(
            f"{self._base_url}/audios",
            headers=self._headers,
            files={
                "audio": (
                    f"voice.{extension}",
                    audio_data,
                    mime_type,
                )
            },
            timeout=180,
        )

        _raise_with_body(response)

        data = response.json()
        audio_url = data.get("url")

        if not audio_url:
            raise ValueError(
                f"D-ID audio upload returned no URL: {data}"
            )

        return audio_url

    def _create_talk(
        self,
        image_url: str,
        audio_url: str,
    ) -> str:
        """Create the asynchronous D-ID avatar generation job."""

        response = requests.post(
            f"{self._base_url}/talks",
            headers={
                **self._headers,
                "Content-Type": "application/json",
            },
            json={
                "source_url": image_url,
                "script": {
                    "type": "audio",
                    "audio_url": audio_url,
                },
                "config": {
                    "stitch": True,
                },
            },
            timeout=180,
        )
        _raise_with_body(response)

        data = response.json()
        talk_id = data.get("id")

        if not talk_id:
            raise ValueError(
                f"D-ID talk response missing id: {data}"
            )

        return talk_id

    def _wait_for_talk(self, talk_id: str) -> str:
        """Poll D-ID until the generated video is ready."""

        deadline = time.monotonic() + self._poll_timeout

        while time.monotonic() < deadline:
            response = requests.get(
                f"{self._base_url}/talks/{talk_id}",
                headers=self._headers,
                timeout=180,
            )
            _raise_with_body(response)

            data = response.json()
            status = data.get("status")

            if status == "done":
                result_url = data.get("result_url")

                if not result_url:
                    raise ValueError(
                        f"D-ID completed without result_url: {data}"
                    )

                return result_url

            if status == "error":
                raise RuntimeError(
                    f"D-ID talk generation failed: {data}"
                )

            time.sleep(self._poll_interval)

        raise TimeoutError(
            f"D-ID talk {talk_id} did not complete within "
            f"{self._poll_timeout} seconds."
        )

    def run(
        self,
        envelope: StageEnvelopeV1,
        run_id: str,
    ) -> StageOutputV1:
        """
        Generate the S30 avatar video from the identity image and S20 audio.
        """

        # The identity reference is supplied to S30 as an image artifact.
        identity_ref = next(
            (
                ref
                for ref in envelope.artifact_refs
                if ref.mime_type in {
                    "image/png",
                    "image/jpeg",
                }
                or "identity_ref" in ref.artifact_id
            ),
            None,
        )

        if identity_ref is None:
            raise ValueError(
                "S30 requires an identity reference image artifact."
            )

        # S30 consumes the audio artifact generated by S20.
        audio_ref = next(
            (
                ref
                for ref in envelope.artifact_refs
                if ref.mime_type in {
                    "audio/mpeg",
                    "audio/mp3",
                    "audio/wav",
                    "audio/x-wav",
                }
                or ref.artifact_id.startswith("voice_")
            ),
            None,
        )

        if audio_ref is None:
            raise ValueError(
                "S30 requires an audio artifact from S20."
            )

        image_data = get_artifact(identity_ref)
        audio_data = get_artifact(audio_ref)
        print(f"[did_avatar] identity image: {len(image_data) / 1_000_000:.2f} MB")
        print(f"[did_avatar] voice audio:    {len(audio_data) / 1_000_000:.2f} MB")

        # D-ID requires URLs for the actual /talks request, so upload
        # both locally stored artifacts to D-ID temporary storage first.
        image_url = self._upload_image(
            image_data,
            identity_ref.mime_type,
        )

        audio_url = self._upload_audio(
            audio_data,
            audio_ref.mime_type,
        )
        print(f"[did_avatar] D-ID image_url: {image_url}")
        print(f"[did_avatar] D-ID audio_url: {audio_url}")

        # Start asynchronous avatar generation.
        talk_id = self._create_talk(
            image_url=image_url,
            audio_url=audio_url,
        )

        # Wait for D-ID to finish and obtain the generated MP4 URL.
        result_url = self._wait_for_talk(talk_id)

        response = requests.get(
            result_url,
            timeout=120,
        )
        _raise_with_body(response)

        video_data = response.content

        if not video_data:
            raise ValueError(
                f"D-ID returned an empty video for talk {talk_id}."
            )

        # Persist the generated video in our normal artifact store.
        artifact = put_artifact(
            data=video_data,
            artifact_id=f"avatar_{run_id}",
            mime_type="video/mp4",
        )

        visual_track = PrimaryVisualTrackV1(
            run_id=run_id,
            video_artifact=artifact,
        )

        return StageOutputV1(
            payload=visual_track.model_dump(),
            metadata={
                "provider": "did",
                "model": "talks",
                "talk_id": talk_id,
            },
            artifact_refs=[artifact],
        )

# --- S30 exit validator logic. Wrapped as a StageValidator and registered
# in orchestrator/stage_executor.py (STAGE_VALIDATORS["S30"]). ---
#
# IMPORTANT SCOPE NOTE (M3 Day 1 Part 1 / Part 2 decision): identity-
# similarity scoring is NOT implemented here. identity_threshold_v1.json
# doesn't exist yet (flagged since M2, still unresolved), and building a
# real face-embedding-similarity check is a separate, heavier task than
# this validator. Per the M3 Day 1 doc's explicit allowance, this ships
# as a placeholder pass-through for identity: it validates that the
# output is a genuinely valid, non-corrupt video of plausible duration,
# but it CANNOT yet detect "right video, wrong face" - only "broken
# video" or "video of the wrong length". Revisit once threshold
# calibration lands.

import os
import subprocess
import tempfile

DURATION_TOLERANCE_SECONDS = 2.0


def _probe_video_streams(video_bytes: bytes) -> list[dict]:
    with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as f:
        f.write(video_bytes)
        tmp_path = f.name
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_streams", tmp_path],
            capture_output=True, text=True, timeout=10,
        )
        data = json.loads(result.stdout)
        return data.get("streams", [])
    except Exception:
        return []
    finally:
        os.unlink(tmp_path)


def validate_avatar_render(
    video_bytes: bytes,
    expected_audio_duration: float | None = None,
    tolerance: float = DURATION_TOLERANCE_SECONDS,
) -> tuple[bool, list[str]]:
    """
    Checks: video file is genuinely valid and non-corrupt (has a real
    video stream), and its duration falls within tolerance of S20's
    audio duration when that's known. Returns (passed, failures).

    Does NOT check identity similarity - see module-level note above.
    """
    failures: list[str] = []

    if not video_bytes:
        return False, ["avatar render is empty (failed generation)"]

    streams = _probe_video_streams(video_bytes)
    if not streams:
        return False, ["avatar render could not be probed - file is corrupt or unreadable"]

    video_stream = next((s for s in streams if s.get("codec_type") == "video"), None)
    if video_stream is None:
        failures.append("no video stream present in avatar render")

    duration = 0.0
    for s in streams:
        if s.get("codec_type") == "video" and s.get("duration"):
            try:
                duration = float(s["duration"])
                break
            except (TypeError, ValueError):
                continue

    if duration <= 0:
        failures.append("avatar render has zero/invalid duration")
    elif expected_audio_duration is not None and expected_audio_duration > 0:
        diff = abs(duration - expected_audio_duration)
        if diff > tolerance:
            failures.append(
                f"avatar render duration {duration:.2f}s differs from expected "
                f"audio duration {expected_audio_duration:.2f}s by {diff:.2f}s "
                f"(tolerance {tolerance}s)"
            )

    return len(failures) == 0, failures