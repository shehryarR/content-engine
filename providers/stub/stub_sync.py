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