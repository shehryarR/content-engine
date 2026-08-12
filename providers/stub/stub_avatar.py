
from __future__ import annotations

import hashlib
import os
from datetime import datetime, timezone
from pathlib import Path

from contracts.common.envelope import ArtifactRefV1, StageEnvelopeV1, StageOutputV1
from contracts.stages.s30_avatar import PrimaryVisualTrackV1
from orchestrator.storage import put_artifact
from providers.base import StageProvider


class StubAvatarProvider(StageProvider):
    

    @property
    def capability(self) -> str:
        return "avatar_render"

    def run(self, envelope: StageEnvelopeV1,run_id:str) -> StageOutputV1:
        
        
        fixture_path = Path(__file__).parent.parent / "fixtures" / "stubs" / "black_5s.mp4"
        
        if not fixture_path.exists():
            raise FileNotFoundError(
                f"Dummy video file not found at {fixture_path}. "
                "Please ensure fixtures/stubs/black_5s.mp4 exists."
            )

        
        with open(fixture_path, "rb") as f:
            video_data = f.read()

        
        artifact_id = f"avatar_{envelope.stage_id}_{envelope.attempt}_{datetime.now(timezone.utc).isoformat()}"
        artifact_ref = put_artifact(
            data=video_data,
            artifact_id=artifact_id,
            mime_type="video/mp4",
        )

        
        visual_track = PrimaryVisualTrackV1(
            run_id=run_id, 
            video_artifact=artifact_ref,
        )

        
        return StageOutputV1(
            payload=visual_track.model_dump(),
            metadata={
                "provider": "stub_avatar",
                "model": "stub_v1",
                "version": "1.0.0",
                "duration_seconds": 5.0,
                "stub": True,
            },
            artifact_refs=[artifact_ref],
        )
