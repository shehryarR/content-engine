"""
Owner E (S50/S60/S70) fixture-triggered validation tests — M3 Day 1.

Confirms the failure fixtures actually get REJECTED by the real
validators registered in STAGE_VALIDATORS, and that their failure_type
matches what pipeline.py's RETRYABLE_VALIDATION_FAILURE_TYPES expects,
i.e. proves these fixtures would trigger (or correctly not trigger) a
local correction retry - not just that validator functions exist.
"""
import json
from pathlib import Path

import pytest

from contracts.common.envelope import (
    ArtifactRefV1,
    ProviderDescriptorV1,
    StageEnvelopeV1,
    StageOutputV1,
)
from orchestrator.pipeline import RETRYABLE_VALIDATION_FAILURE_TYPES
from orchestrator.stage_executor import (
    _validate_captions_stage,
    _validate_assembly_stage,
)
from orchestrator.storage import put_artifact


def _make_envelope(stage_id, capability, artifact_refs=None):
    return StageEnvelopeV1(
        stage_id=stage_id,
        attempt=1,
        input_hash="a" * 64,
        artifact_refs=artifact_refs or [],
        validation_ref=None,
        provider=ProviderDescriptorV1(
            provider=capability, model="test", version="1.0.0", capability=capability,
        ),
    )


def test_corrupt_caption_timing_fixture_fails_and_is_retryable():
    fixture_path = Path("fixtures/failures/corrupt_caption_timing.json")
    caption_bytes = fixture_path.read_bytes()
    caption_artifact = put_artifact(
        data=caption_bytes, artifact_id="test_corrupt_captions", mime_type="application/json",
    )

    output = StageOutputV1(
        payload=json.loads(caption_bytes),
        metadata={},
        artifact_refs=[caption_artifact],
    )
    envelope = _make_envelope("S50", "caption_generation", artifact_refs=[])

    report = _validate_captions_stage(output, "S50", envelope)

    assert report.passed is False, "corrupt caption fixture should fail validation"
    assert report.failure_type == "caption_timing_invalid"
    assert report.failure_type in RETRYABLE_VALIDATION_FAILURE_TYPES, (
        "caption_timing_invalid must be retryable per M3 Day 1 success criteria"
    )


def test_failed_encode_fixture_fails_and_is_retryable():
    fixture_path = Path("fixtures/failures/failed_encode.mp4")
    video_bytes = fixture_path.read_bytes()
    video_artifact = put_artifact(
        data=video_bytes, artifact_id="test_failed_encode", mime_type="video/mp4",
    )

    output = StageOutputV1(
        payload={},
        metadata={},
        artifact_refs=[video_artifact],
    )
    envelope = _make_envelope("S60", "assembly", artifact_refs=[])

    report = _validate_assembly_stage(output, "S60", envelope)

    assert report.passed is False, "failed-encode fixture (garbage bytes) should fail validation"
    assert report.failure_type in ("assembly_failed", "assembly_duration_mismatch")
    assert report.failure_type in RETRYABLE_VALIDATION_FAILURE_TYPES, (
        "assembly failures must be retryable per M3 Day 1 Part 5 decision"
    )


def test_disclosure_false_synthetic_flag_fails():
    """G90: an avatar-modality disclosure decision with
    contains_synthetic_media=False must fail validation."""
    from contracts.stages.g90_disclosure import DisclosureDecisionV1
    from orchestrator.stage_executor import _validate_disclosure_stage

    bad_disclosure = DisclosureDecisionV1(
        modality="avatar",
        master_video_hash="a" * 64,
        contains_synthetic_media=False,  # violates the M0/M1 rule
        policy_basis="policy_stub_g90",
    )
    output = StageOutputV1(
        payload=bad_disclosure.model_dump(mode="json"),
        metadata={},
        artifact_refs=[],
    )
    envelope = _make_envelope("G90", "disclosure_check", artifact_refs=[])

    report = _validate_disclosure_stage(output, "G90", envelope)

    assert report.passed is False
    assert report.failure_type == "disclosure_synthetic_flag_false"
    assert report.failure_type not in RETRYABLE_VALIDATION_FAILURE_TYPES, (
        "a synthetic-flag violation is a policy issue, not something a "
        "blind retry fixes - must not be retryable"
    )


def test_publish_public_privacy_fails():
    """S100: privacy='public' must fail validation, regardless of
    upstream disclosure state."""
    from contracts.stages.s100_publish import PublishReceiptV1
    from orchestrator.stage_executor import _validate_publish_stage

    bad_receipt = PublishReceiptV1(
        run_id="test_run_s100",
        platform_video_id=None,
        privacy="public",  # never allowed
        dry_run=True,
    )
    output = StageOutputV1(
        payload=bad_receipt.model_dump(mode="json"),
        metadata={},
        artifact_refs=[],
    )
    envelope = _make_envelope("S100", "publish", artifact_refs=[])

    report = _validate_publish_stage(output, "S100", envelope)

    assert report.passed is False
    assert report.failure_type == "publish_privacy_violation"
    assert report.failure_type not in RETRYABLE_VALIDATION_FAILURE_TYPES
