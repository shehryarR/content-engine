import json

from temporalio import activity
from temporalio.exceptions import ApplicationError

from contracts.common.envelope import StageEnvelopeV1
from contracts.common.manifest import StageRecordV1, StageStatus
from contracts.common.envelope import ArtifactRefV1
from contracts.common.validation_failure import ValidationFailureV1
from orchestrator.manifest_store import save_stage_record
from orchestrator.stage_executor import execute_stage
from orchestrator.storage import put_artifact
from orchestrator.consent_gate import _fetch_one


@activity.defn
async def run_stage(
    capability: str,
    envelope_dict: dict,
    run_id: str,
    idea_request_id: str,
) -> dict:
    envelope = StageEnvelopeV1.model_validate(envelope_dict)
    output, validation_ref = execute_stage(run_id, idea_request_id, capability, envelope)
    result = output.model_dump()
    # Pass validation_ref back so the pipeline can attach it to the next envelope
    result["_validation_ref"] = validation_ref.model_dump()
    return result


@activity.defn
async def record_g80_approval(
    run_id: str,
    idea_request_id: str,
    started_at: str,
    completed_at: str,
    approval_decision: dict,
) -> None:
    """
    Persist the G80 human-approval gate as a manifest row, and enforce
    the decision itself.

    Previously this unconditionally wrote a PASSED row regardless of what
    the reviewer actually decided - a REJECTED or CHANGES_REQUESTED
    HumanApprovalV1 was recorded identically to an APPROVED one, and the
    pipeline carried on to G90/S100 either way. Now: APPROVED writes
    PASSED and returns normally. Anything else writes FAILED (with an
    evidence artifact of the raw decision) and raises the same
    ValidationFailureV1 / ApplicationError pattern every other stage's
    failure uses - non_retryable, since a rejected approval needs a real
    human decision, not an automatic retry - so it stops the workflow
    with the same audit trail every other failure gets, instead of a
    silent PASSED row.
    """
    decision = approval_decision.get("decision")

    if decision == "approved":
        save_stage_record(
            run_id=run_id,
            idea_request_id=idea_request_id,
            stage=StageRecordV1(
                stage_id="G80",
                status=StageStatus.PASSED,
                attempt=1,
                started_at=started_at,
                completed_at=completed_at,
            ),
        )
        return

    evidence_bytes = json.dumps(approval_decision).encode("utf-8")
    evidence_ref = put_artifact(
        data=evidence_bytes,
        artifact_id=f"g80_approval_{run_id}_FAILED",
        mime_type="application/json",
    )
    save_stage_record(
        run_id=run_id,
        idea_request_id=idea_request_id,
        stage=StageRecordV1(
            stage_id="G80",
            status=StageStatus.FAILED,
            attempt=1,
            started_at=started_at,
            completed_at=completed_at,
        ),
    )
    failure = ValidationFailureV1(
        stage_id="G80",
        failure_type="approval_rejected",
        message=f"G80 approval decision was {decision!r}, not 'approved'.",
        evidence_ref=evidence_ref,
        retryable=False,
    )
    raise ApplicationError(
        failure.message,
        failure.model_dump(mode="json"),
        type="ValidationFailure",
        non_retryable=True,
    )

@activity.defn
async def run_intake_stage(idea_dict: dict, run_id: str, envelope_dict: dict) -> dict:
    envelope = StageEnvelopeV1.model_validate(envelope_dict)
    output, validation_ref = execute_stage(
        run_id, run_id, "intake", envelope, idea_dict=idea_dict
    )
    result = output.model_dump()
    result["_validation_ref"] = validation_ref.model_dump()
    return result

@activity.defn
async def fetch_identity_reference(identity_id: str) -> dict:
    row = _fetch_one(
        "SELECT reference_asset, reference_sample_hash, consent_status "
        "FROM identity_profiles WHERE identity_id=%s",
        (identity_id,),
    )
    if row is None:
        raise ValueError(f"identity_id {identity_id} not found in registry")
    reference_path, reference_hash, consent_status = row
    if consent_status != "active":
        raise ValueError(f"identity_id {identity_id} consent status is {consent_status}, not active")
    if not reference_hash:
        raise ValueError(
            f"identity_id {identity_id} has no reference_sample_hash — "
            f"reseed via seed_identity_reference.py"
        )

    return ArtifactRefV1(
        artifact_id=f"identity_ref_{identity_id}",
        path=reference_path,
        hash=reference_hash,
        mime_type="image/png",
    ).model_dump()