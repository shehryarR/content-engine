from __future__ import annotations

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

    def __init__(self):
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
            "Authorization": f"Basic {self._api_key}",
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

        response.raise_for_status()

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

        response.raise_for_status()

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
            },
            timeout=180,
        )

        response.raise_for_status()

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

            response.raise_for_status()

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
        response.raise_for_status()

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