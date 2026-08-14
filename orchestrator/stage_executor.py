"""
orchestrator/stage_executor.py

Wraps a provider's run() call with manifest persistence and telemetry
recording. Does NOT call put_artifact() itself for provider artifacts -
providers are responsible for storing their own artifacts and returning
real ArtifactRefV1 objects in their StageOutputV1.

The executor does call put_artifact once per stage to store the
ValidationReportV1 — this is the executor's own responsibility, not
the provider's.
"""

import hashlib
import json
from datetime import datetime, timezone
from typing import Callable

from temporalio.exceptions import ApplicationError

from contracts.common.envelope import ArtifactRefV1, StageEnvelopeV1, StageOutputV1, ValidationReportV1
from contracts.common.manifest import StageRecordV1, StageStatus
from contracts.common.telemetry import StageRunRecordV1
from contracts.common.validation_failure import ValidationFailureV1
from graph.pipeline_graph import STAGE_SEQUENCE
from orchestrator.manifest_store import save_stage_record
from orchestrator.registry import get as get_provider
from orchestrator.storage import get_artifact, put_artifact
from orchestrator.telemetry import record_telemetry

# In-memory, process-lifetime cache of verified stage results, keyed by
# canonical input (envelope.input_hash + provider descriptor + upstream
# artifact hashes). Lets a stage re-execution (e.g. a local correction
# retry, or an activity-level re-run) reuse a prior attempt's already-
# verified artifacts instead of re-invoking the provider.
_stage_result_cache: dict[str, tuple[StageOutputV1, ArtifactRefV1]] = {}


def _compute_cache_key(envelope: StageEnvelopeV1) -> str:
    key_material = {
        "input_hash": envelope.input_hash,
        "provider": envelope.provider.model_dump(mode="json"),
        "upstream_hashes": sorted(ref.hash for ref in envelope.artifact_refs),
    }
    return hashlib.sha256(
        json.dumps(key_material, sort_keys=True).encode("utf-8")
    ).hexdigest()


def _verify_artifact_hashes(
    output: StageOutputV1, stage_id: str, envelope: StageEnvelopeV1
) -> ValidationReportV1:
    """
    Checkpoint step: re-fetch every artifact the provider claims to have
    stored, recompute its SHA-256, and confirm it matches ref.hash before
    anything gets persisted. Returns a ValidationReportV1 recording the
    result. A stage cannot be marked PASSED until this report exists and
    passed=True.

    `envelope` is accepted (and unused here) only to satisfy the shared
    StageValidator signature - this validator has no need for envelope
    context, but every entry in STAGE_VALIDATORS must be callable the
    same way so execute_stage doesn't need to special-case any of them.
    """
    failures = []
    for ref in output.artifact_refs:
        try:
            stored_bytes = get_artifact(ref)
            actual_hash = hashlib.sha256(stored_bytes).hexdigest()
            if actual_hash != ref.hash:
                failures.append(
                    f"Hash mismatch for {ref.artifact_id}: expected {ref.hash}, got {actual_hash}"
                )
        except Exception as exc:
            failures.append(f"Could not fetch artifact {ref.artifact_id}: {exc}")

    return ValidationReportV1(
        passed=len(failures) == 0,
        failures=failures,
        stage_id=stage_id,
        failure_type="hash_mismatch" if failures else None,
    )


# Per-stage validator registration. Every stage in STAGE_SEQUENCE gets a
# slot here; today they all resolve to the generic hash-check. Stage-
# specific validation logic (beyond hash verification) plugs in by
# overriding a stage's entry, not by changing execute_stage.
#
# A custom validator receives the stage's output, the stage_id, and the
# full envelope (so it can inspect envelope.artifact_refs - e.g. to find
# the upstream audio artifact by mime_type - or envelope.provider). Note
# envelope.artifact_refs only carries ArtifactRefV1s (id/path/hash/mime_type)
# accumulated from prior stages' outputs, not their full payloads, so a
# field like S20's duration_seconds is NOT available on the envelope
# directly - pipeline.py only forwards artifact_refs between stages, not
# payload. A validator that needs duration has to fetch the audio artifact
# itself and derive it (e.g. via ffprobe), the same way stub_assembly.py's
# duration measurement already does.
#
# A failing validator should set ValidationReportV1.failure_type to its own
# machine-readable category (e.g. "duration_mismatch") rather than leaving
# it None. execute_stage falls back to a generic label when a validator
# doesn't set one, but that fallback is deliberately NOT "hash_mismatch" -
# a validator's failure shouldn't be mislabeled as a hash failure just
# because none was given. Downstream, pipeline.py's
# RETRYABLE_VALIDATION_FAILURE_TYPES only treats known types as
# locally-retryable, so a new failure_type needs to be added there
# explicitly for the correction loop to retry on it.
StageValidator = Callable[[StageOutputV1, str, StageEnvelopeV1], ValidationReportV1]

STAGE_VALIDATORS: dict[str, StageValidator] = {
    stage_id: _verify_artifact_hashes for stage_id, _capability in STAGE_SEQUENCE
}


def execute_stage(
    run_id: str,
    idea_request_id: str,
    capability: str,
    envelope: StageEnvelopeV1,idea_dict:dict | None=None
) -> tuple[StageOutputV1, ArtifactRefV1]:
    """
    Run one stage's provider, verify its output artifacts against storage,
    store a ValidationReportV1 to MinIO, then record both the manifest row
    and the telemetry row for this execution.

    Returns (output, validation_ref) so the pipeline can pass validation_ref
    into the next stage's envelope.
    """
    started_at = datetime.now(timezone.utc)

    provider = get_provider(capability)
    cache_key = _compute_cache_key(envelope)
    cached = _stage_result_cache.get(cache_key)

    if cached is not None:
        output, validation_ref = cached
    else:
        if idea_dict is not None:
            output = provider.run(envelope, run_id, idea_dict)
        else:
            output = provider.run(envelope, run_id)

        # Checkpoint promotion: validation must pass before PASSED is written
        validator = STAGE_VALIDATORS.get(envelope.stage_id, _verify_artifact_hashes)
        validation_report = validator(output, envelope.stage_id, envelope)

        if not validation_report.passed:
            failure_type = validation_report.failure_type or "unspecified_validation_failure"
            failure = ValidationFailureV1(
                stage_id=envelope.stage_id,
                failure_type=failure_type,
                message=f"Stage {envelope.stage_id} failed validation ({failure_type}): {validation_report.failures}",
                feedback_context="; ".join(validation_report.failures),
            )
            # non_retryable: Temporal's own retry policy would just replay
            # this exact envelope; the correction retry (if any) belongs to
            # pipeline.py, which re-invokes this stage with attempt += 1.
            raise ApplicationError(
                failure.message,
                failure.model_dump(mode="json"),
                type="ValidationFailure",
                non_retryable=True,
            )

        report_bytes = validation_report.model_dump_json().encode("utf-8")
        validation_ref = put_artifact(
            data=report_bytes,
            artifact_id=f"validation_{envelope.stage_id}_attempt{envelope.attempt}",
            mime_type="application/json",
        )
        _stage_result_cache[cache_key] = (output, validation_ref)

    ended_at = datetime.now(timezone.utc)

    output_hash = output.artifact_refs[0].hash if output.artifact_refs else None
    output_artifact_ids = [ref.artifact_id for ref in output.artifact_refs]

    stage_record = StageRecordV1(
        stage_id=envelope.stage_id,
        status=StageStatus.PASSED,
        attempt=envelope.attempt,
        started_at=started_at,
        completed_at=ended_at,
        output_artifact_ids=output_artifact_ids,
    )
    save_stage_record(run_id, idea_request_id, stage_record)

    telemetry_record = StageRunRecordV1(
        run_id=run_id,
        stage_id=envelope.stage_id,
        attempt=envelope.attempt,
        input_hash=envelope.input_hash,
        output_hash=output_hash,
        provider=envelope.provider,
        started_at=started_at,
        ended_at=ended_at,
    )
    record_telemetry(telemetry_record)

    return output, validation_ref

# --- Owner E (S50/S60/S70) validator wrappers ---
# Each wraps the stage's own validate_*() logic (living in the provider
# file, per the M3 Day 1 doc's "Where" guidance) to match StageValidator's
# 3-arg signature, then gets registered into STAGE_VALIDATORS below.

from providers.real.openai_whisper_captions import validate_captions
from providers.real.assembly import validate_assembly, _measure_duration


def _get_upstream_audio_duration(envelope: StageEnvelopeV1) -> float:
    """Fetch the upstream S20 audio artifact (if present in
    envelope.artifact_refs) and measure its duration via ffprobe."""
    audio_ref = next(
        (r for r in envelope.artifact_refs
         if r.mime_type and r.mime_type.startswith("audio/")),
        None,
    )
    if audio_ref is None:
        return 0.0
    audio_bytes = get_artifact(audio_ref)
    return _measure_duration(audio_bytes)


def _validate_captions_stage(
    output: StageOutputV1, stage_id: str, envelope: StageEnvelopeV1
) -> ValidationReportV1:
    if not output.artifact_refs:
        return ValidationReportV1(
            passed=False,
            failures=["no caption artifact produced"],
            stage_id=stage_id,
            failure_type="caption_timing_invalid",
        )
    caption_bytes = get_artifact(output.artifact_refs[0])
    captions_data = json.loads(caption_bytes)
    audio_duration = _get_upstream_audio_duration(envelope)

    passed, failures = validate_captions(captions_data, audio_duration)
    return ValidationReportV1(
        passed=passed,
        failures=failures,
        stage_id=stage_id,
        failure_type=None if passed else "caption_timing_invalid",
    )


def _validate_assembly_stage(
    output: StageOutputV1, stage_id: str, envelope: StageEnvelopeV1
) -> ValidationReportV1:
    if not output.artifact_refs:
        return ValidationReportV1(
            passed=False,
            failures=["no video artifact produced (failed encode)"],
            stage_id=stage_id,
            failure_type="assembly_failed",
        )
    video_bytes = get_artifact(output.artifact_refs[0])
    audio_duration = _get_upstream_audio_duration(envelope)

    passed, failures = validate_assembly(
        video_bytes,
        expected_audio_duration=audio_duration if audio_duration > 0 else None,
    )
    return ValidationReportV1(
        passed=passed,
        failures=failures,
        stage_id=stage_id,
        failure_type=None if passed else "assembly_duration_mismatch",
    )


def _validate_qc_stage(
    output: StageOutputV1, stage_id: str, envelope: StageEnvelopeV1
) -> ValidationReportV1:
    """S70 QC already computes its own pass/fail (stub_qc.py's Layer 1/4
    checks) — this wrapper just surfaces that result into the shared
    ValidationReportV1/correction-loop flow rather than re-deriving it."""
    qc_passed = bool(output.metadata.get("passed", False))
    metrics = output.payload.get("metrics", {}) if isinstance(output.payload, dict) else {}
    failed_checks = [k for k, v in metrics.items() if isinstance(v, float) and v == 0.0]

    return ValidationReportV1(
        passed=qc_passed,
        failures=failed_checks if failed_checks else (["QC failed"] if not qc_passed else []),
        stage_id=stage_id,
        failure_type=None if qc_passed else "qc_failed",
    )


STAGE_VALIDATORS["S50"] = _validate_captions_stage
STAGE_VALIDATORS["S60"] = _validate_assembly_stage
STAGE_VALIDATORS["S70"] = _validate_qc_stage
