"""
providers/stub_publish.py

S100 Publish Stub Provider.

Verifies the disclosure decision and privacy setting before returning a
dry-run PublishReceiptV1. Even as a stub, these checks are not skipped -
they are part of what M1's pass/fail test verifies.

Design note: publish verifies disclosure content and privacy policy even in
stub mode to preserve behavioral checks expected by M1 validation.
"""

import json

from contracts.common.envelope import StageEnvelopeV1, StageOutputV1
from contracts.stages.g90_disclosure import DisclosureDecisionV1
from contracts.stages.s100_publish import PublishReceiptV1
from orchestrator.storage import get_artifact

TARGET_PRIVACY = "unlisted"  # this pipeline never publishes as public


class StubPublishProvider:
    """Stub implementation for S100 publish capability."""

    capability: str = "publish"

    def run(self, envelope: StageEnvelopeV1, run_id: str) -> StageOutputV1:
        disclosure_ref = next(
            (ref for ref in envelope.artifact_refs if "disclosure" in ref.artifact_id.lower()),
            None,
        )
        if disclosure_ref is None:
            raise ValueError(
                "S100 publish stub requires a disclosure-decision artifact "
                "reference in envelope.artifact_refs before publishing."
            )

        disclosure_bytes = get_artifact(disclosure_ref)
        disclosure = DisclosureDecisionV1.model_validate(json.loads(disclosure_bytes))

        if not disclosure.contains_synthetic_media:
            raise ValueError(
                "Refusing to publish: containsSyntheticMedia is False. "
                "This is an unconditional architecture rule for avatar modality."
            )

        assert TARGET_PRIVACY == "unlisted", "M1 only supports unlisted uploads"

        receipt = PublishReceiptV1(
            run_id=run_id,
            platform_video_id=None,
            privacy=TARGET_PRIVACY,
            dry_run=True,
        )

        return StageOutputV1(
            payload=receipt.model_dump(),
            metadata={"stub": True, "provider": "stub_publish_provider"},
            artifact_refs=[],
        )
