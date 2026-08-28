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
    """Stub implementation for S100 publish capability.

    M5 step 7: class-level invocation counter so the negative test suite
    (tests/test_m5_gate_negative.py, Fatima) can assert publish was never
    actually called for any of the 5 gate-blocking scenarios. Class-level
    (not instance-level) so the count persists across a test's multiple
    StubPublishProvider() instantiations - reset_count() must be called
    in each test's setup, or counts leak across tests sharing this class.
    """

    capability: str = "publish"

    _invocation_count: int = 0

    @classmethod
    def reset_count(cls):
        cls._invocation_count = 0

    @classmethod
    def get_count(cls) -> int:
        return cls._invocation_count

    def run(self, envelope: StageEnvelopeV1, run_id: str) -> StageOutputV1:
        StubPublishProvider._invocation_count += 1

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
