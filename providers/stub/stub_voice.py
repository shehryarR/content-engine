"""
providers/stub_voice.py

Stub provider for S20 voice generation.
Returns a VoiceTrackV1 with a reference to a pre-made dummy audio file.
"""

from __future__ import annotations

import hashlib
import os
from datetime import datetime, timezone
from pathlib import Path

from contracts.common.envelope import ArtifactRefV1, StageEnvelopeV1, StageOutputV1
from contracts.stages.s20_voice import VoiceTrackV1
from orchestrator.storage import put_artifact
from providers.base import StageProvider


class StubVoiceProvider(StageProvider):
    """Stub voice provider that returns a reference to a pre-made dummy audio file."""

    @property
    def capability(self) -> str:
        return "voice_synthesis"

    def run(self, envelope: StageEnvelopeV1,run_id:str) -> StageOutputV1:
        """
        Return a VoiceTrackV1 with a reference to a stored dummy audio file.

        Reads the pre-made silent 5-second WAV from fixtures/stubs/,
        stores it in MinIO via storage.py, and returns a real ArtifactRefV1.
        """
        # Get the dummy audio file path
        fixture_path = Path(__file__).parent.parent / "fixtures" / "stubs" / "silent_5s.wav"
        
        if not fixture_path.exists():
            raise FileNotFoundError(
                f"Dummy audio file not found at {fixture_path}. "
                "Please ensure fixtures/stubs/silent_5s.wav exists."
            )

        # Read the file
        with open(fixture_path, "rb") as f:
            audio_data = f.read()

        # Store in MinIO via storage.py
        artifact_id = f"voice_{envelope.stage_id}_{envelope.attempt}_{datetime.now(timezone.utc).isoformat()}"
        artifact_ref = put_artifact(
            data=audio_data,
            artifact_id=artifact_id,
            mime_type="audio/wav",
        )

        # Build the VoiceTrackV1 payload
        voice_track = VoiceTrackV1(
            run_id=run_id,  # Using stage_id as run_id for stub
            voice_id="stub_voice_001",
            audio_artifact=artifact_ref,
            duration_seconds=5.0,  # Fixed 5-second dummy audio
        )

        # Return StageOutputV1 with the payload as dict
        return StageOutputV1(
            payload=voice_track.model_dump(),
            metadata={
                "provider": "stub_voice",
                "model": "stub_v1",
                "version": "1.0.0",
                "duration_seconds": 5.0,
                "stub": True,
            },
            artifact_refs=[artifact_ref],
        )
