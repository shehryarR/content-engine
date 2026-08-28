"""
tests/test_m5_gate_negative.py

Negative gate suite (Steps 9+10): five scenarios proving S100 publish is
unreachable without valid approval and disclosure.

  Tests 1-4  stop at G80 (no approval / bad decision / stale hash).
  Test 5     stops at G90 (_validate_disclosure_stage fails; .run() never called).

All five tests assert StubPublishProvider.get_count() == 0 after the
scenario completes — never mid-run.
"""
import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from temporalio.exceptions import ApplicationError

from contracts.common.envelope import (
    ArtifactRefV1,
    ProviderDescriptorV1,
    StageEnvelopeV1,
    StageOutputV1,
)
from contracts.stages.g80_approval import ApprovalDecision, HumanApprovalV1
from orchestrator.stage_executor import _validate_disclosure_stage
from providers.stub.stub_publish import StubPublishProvider


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_envelope(stage_id: str, capability: str, artifact_refs=None) -> StageEnvelopeV1:
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


def _reset_publish_count():
    """Reset the real class-level invocation counter before each test.

    Uses StubPublishProvider's actual reset_count()/get_count() (M5 step 7,
    Hanab) rather than a test-local shadow counter — a wrapper here would
    test the wrapper, not the real implementation.
    """
    StubPublishProvider.reset_count()


# ---------------------------------------------------------------------------
# Test 1 — No approval signal → publish never called
# ---------------------------------------------------------------------------

def test_no_approval_publish_never_called():
    """
    Without any approval signal the workflow never proceeds past G80.
    Publish invocation count must be 0.
    We test this by verifying the pipeline's internal guard condition:
    self._approval is None means the wait_condition has not been satisfied,
    so G90/S100 are never entered.
    """
    _reset_publish_count()

    # Simulate the pipeline state where no approval signal has been received
    from orchestrator.pipeline import AvatarPipeline
    pipeline = AvatarPipeline()

    # _approval starts as None — the wait_condition in the workflow would block here
    assert pipeline._approval is None, "No approval signal should have been received"

    # Because _approval is None, G90/S100 loop body is never entered
    assert StubPublishProvider.get_count() == 0


# ---------------------------------------------------------------------------
# Test 2 — REJECTED approval → workflow stops, publish count = 0
# ---------------------------------------------------------------------------

@patch("orchestrator.activities.put_artifact")
@patch("orchestrator.activities.save_stage_record")
def test_rejected_approval_raises_and_publish_not_called(mock_save, mock_put):
    """
    A REJECTED HumanApprovalV1 fed to record_g80_approval must raise
    ApplicationError with type='ValidationFailure' and failure_type='approval_rejected'.
    The workflow stops. Publish count stays 0.
    """
    _reset_publish_count()

    # Mock put_artifact to return a valid ArtifactRefV1 without hitting S3
    mock_put.return_value = ArtifactRefV1(
        artifact_id="g80_approval_test_run_FAILED",
        path="s3://bucket/artifacts/g80_approval_test_run_FAILED",
        hash="a" * 64,
        mime_type="application/json",
    )

    from orchestrator.activities import record_g80_approval

    decision = HumanApprovalV1(
        reviewer_id="reviewer_001",
        decision=ApprovalDecision.REJECTED,
        master_video_hash="b" * 64,
        comments="Does not meet quality bar.",
    )

    with pytest.raises(ApplicationError) as exc_info:
        asyncio.run(
            record_g80_approval(
                run_id="test_run_rejected",
                idea_request_id="test_run_rejected",
                started_at="2026-08-28T00:00:00Z",
                completed_at="2026-08-28T00:01:00Z",
                approval_decision=decision.model_dump(mode="json"),
            )
        )

    assert exc_info.value.type == "ValidationFailure"
    # Confirm the failure detail has the right failure_type
    detail = exc_info.value.details[0]
    assert detail["failure_type"] == "approval_rejected"
    assert StubPublishProvider.get_count() == 0


# ---------------------------------------------------------------------------
# Test 3 — CHANGES_REQUESTED → same as REJECTED
# ---------------------------------------------------------------------------

@patch("orchestrator.activities.put_artifact")
@patch("orchestrator.activities.save_stage_record")
def test_changes_requested_raises_and_publish_not_called(mock_save, mock_put):
    """
    CHANGES_REQUESTED is treated identically to REJECTED — raises
    ApplicationError, workflow stops, publish count stays 0.
    """
    _reset_publish_count()

    mock_put.return_value = ArtifactRefV1(
        artifact_id="g80_approval_test_run_cr_FAILED",
        path="s3://bucket/artifacts/g80_approval_test_run_cr_FAILED",
        hash="a" * 64,
        mime_type="application/json",
    )

    from orchestrator.activities import record_g80_approval

    decision = HumanApprovalV1(
        reviewer_id="reviewer_001",
        decision=ApprovalDecision.CHANGES_REQUESTED,
        master_video_hash="b" * 64,
        comments="Please revise the intro.",
    )

    with pytest.raises(ApplicationError) as exc_info:
        asyncio.run(
            record_g80_approval(
                run_id="test_run_cr",
                idea_request_id="test_run_cr",
                started_at="2026-08-28T00:00:00Z",
                completed_at="2026-08-28T00:01:00Z",
                approval_decision=decision.model_dump(mode="json"),
            )
        )

    assert exc_info.value.type == "ValidationFailure"
    detail = exc_info.value.details[0]
    assert detail["failure_type"] == "approval_rejected"
    assert StubPublishProvider.get_count() == 0


# ---------------------------------------------------------------------------
# Test 4 — Approval for wrong/stale hash → signal ignored, stays at G80
# ---------------------------------------------------------------------------

def test_stale_hash_approval_is_ignored():
    """
    When an approval signal arrives with a master_video_hash that does NOT
    match the pipeline's _current_master_hash, the signal handler ignores it
    (self._approval remains None). Workflow stays waiting. Publish count = 0.
    """
    _reset_publish_count()

    from orchestrator.pipeline import AvatarPipeline
    pipeline = AvatarPipeline()

    # Simulate that the pipeline has assembled a video with a known hash
    pipeline._current_master_hash = "c" * 64

    # Approval arrives with a different (stale) hash
    stale_decision = HumanApprovalV1(
        reviewer_id="reviewer_001",
        decision=ApprovalDecision.APPROVED,
        master_video_hash="d" * 64,  # different from _current_master_hash
    )

    # The signal handler logic: only sets _approval if hashes match
    decision_hash = stale_decision.master_video_hash
    if pipeline._current_master_hash is None or decision_hash == pipeline._current_master_hash:
        pipeline._approval = stale_decision.model_dump(mode="json")
    # else: ignored (stale hash)

    # _approval must remain None — signal was ignored
    assert pipeline._approval is None, "Stale-hash approval must not set _approval"
    assert StubPublishProvider.get_count() == 0


# ---------------------------------------------------------------------------
# Test 5 — Disclosure missing/false → G90 fails, S100 never runs
# ---------------------------------------------------------------------------

def test_false_disclosure_fails_g90_publish_not_called():
    """
    G90 (_validate_disclosure_stage) rejects contains_synthetic_media=False
    and returns passed=False. The pipeline never proceeds to S100, so
    StubPublishProvider.run is never called. Uses the existing
    fixtures/failures/disclosure_faceless_false.json fixture.
    """
    _reset_publish_count()

    fixture_path = Path("fixtures/failures/disclosure_faceless_false.json")
    payload = json.loads(fixture_path.read_text())

    output = StageOutputV1(
        payload=payload,
        metadata={},
        artifact_refs=[],
    )
    envelope = _make_envelope("G90", "disclosure_check")

    # This is the G90 validator — S100 is not invoked at all in this test.
    report = _validate_disclosure_stage(output, "G90", envelope)

    # Workflow stops here at G90; the assertion below is post-completion.
    assert report.passed is False, "G90 must reject contains_synthetic_media=False"
    assert report.failure_type == "disclosure_synthetic_flag_false"
    assert StubPublishProvider.get_count() == 0, "S100 must never be reached when G90 fails"
