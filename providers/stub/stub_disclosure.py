"""
providers/stub_disclosure.py

G90 Disclosure Decision Stub Provider.

Produces a deterministic DisclosureDecisionV1 payload wrapped inside a StageOutputV1.
"""

from contracts.common.envelope import StageEnvelopeV1, StageOutputV1
from contracts.stages.g90_disclosure import DisclosureDecisionV1
from contracts.stages.idea_request import Modality
from orchestrator.storage import put_artifact


class StubDisclosureProvider:
    """
    Stub implementation for G90 disclosure check capability.

    Always returns a deterministic DisclosureDecisionV1 payload with
    contains_synthetic_media=True for avatar modality runs.
    """

    capability: str = "disclosure_check"

    def run(self, envelope: StageEnvelopeV1, run_id: str) -> StageOutputV1:
        video_ref = next(
            (
                ref
                for ref in envelope.artifact_refs
                if ref.mime_type and ref.mime_type.startswith("video/")
            ),
            None,
        )
        if video_ref is None:
            raise ValueError(
                "G90 disclosure stub requires a video artifact in envelope.artifact_refs"
            )
        master_video_hash = video_ref.hash
        modality_val = Modality.AVATAR

        disclosure_decision = DisclosureDecisionV1(
            modality=modality_val,
            master_video_hash=master_video_hash,
            contains_synthetic_media=True,
            policy_basis="policy_stub_g90",
        )
        disclosure_bytes = disclosure_decision.model_dump_json().encode("utf-8")
        artifact = put_artifact(
            data=disclosure_bytes,
            artifact_id=f"disclosure_{run_id}",
            mime_type="application/json",
        )

        return StageOutputV1(
            payload=disclosure_decision.model_dump(mode="json"),
            metadata={
                "stub": True,
                "provider": "stub_disclosure_provider",
                "contains_synthetic_media": disclosure_decision.contains_synthetic_media,
            },
            artifact_refs=[artifact],
        )
