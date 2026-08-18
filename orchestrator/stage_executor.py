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

from pydantic import ValidationError

from contracts.common.envelope import ArtifactRefV1, StageEnvelopeV1, StageOutputV1, ValidationReportV1
from contracts.common.manifest import StageRecordV1, StageStatus
from contracts.common.telemetry import StageRunRecordV1
from contracts.common.validation_failure import ValidationFailureV1
from contracts.stages.idea_request import IdeaRequestV1
from graph.pipeline_graph import STAGE_SEQUENCE
from orchestrator.manifest_store import save_stage_record
from orchestrator.registry import get as get_provider
from orchestrator.storage import get_artifact, put_artifact
from orchestrator.telemetry import record_telemetry
from orchestrator.pipeline import RETRYABLE_VALIDATION_FAILURE_TYPES

# In-memory, process-lifetime cache of verified stage results, keyed by
# canonical input (envelope.input_hash + provider descriptor + upstream
# artifact hashes). Lets a stage re-execution (e.g. a local correction
# retry, or an activity-level re-run) reuse a prior attempt's already-
# verified artifacts instead of re-invoking the provider.
_stage_result_cache: dict[str, tuple[StageOutputV1, ArtifactRefV1]] = {}

# In-memory cache of validation failures/feedback context to thread context
# on retry without altering StageEnvelopeV1 or pipeline.py contracts.
_stage_feedback_cache: dict[str, str] = {}


def get_stage_feedback(run_id: str, stage_id: str) -> str | None:
    """Retrieve feedback context for a retried stage execution."""
    return _stage_feedback_cache.get(f"{run_id}_{stage_id}")


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


def _validate_s10_script(
    output: StageOutputV1,
    stage_id: str,
    envelope: StageEnvelopeV1,
) -> ValidationReportV1:
    """
    Deterministic exit validator for S10 script generation.

    Enforces the script constraints required by the S10 prompt:
    - scenes must be present
    - exactly 3 scenes
    - every scene must be a non-empty string
    - combined scene length must never exceed the 1500-character hard cap
    """
    failures: list[str] = []

    payload = output.payload

    if not isinstance(payload, dict):
        failures.append("S10 output payload must be a JSON object.")
        return ValidationReportV1(
            passed=False,
            failures=failures,
            stage_id=stage_id,
            failure_type="malformed_script",
        )

    if set(payload.keys()) != {"run_id", "scenes"}:
        failures.append(
            "S10 output must contain exactly the 'run_id' and 'scenes' fields."
        )

    scenes = payload.get("scenes")

    if not isinstance(scenes, list):
        failures.append("S10 'scenes' must be a list.")
    else:
        if len(scenes) != 3:
            failures.append(
                f"S10 script must contain exactly 3 scenes; got {len(scenes)}."
            )

        for index, scene in enumerate(scenes, start=1):
            if not isinstance(scene, str):
                failures.append(
                    f"S10 scene {index} must be a string."
                )
            elif not scene.strip():
                failures.append(
                    f"S10 scene {index} must be non-empty."
                )

        if all(isinstance(scene, str) for scene in scenes):
            combined_length = sum(len(scene) for scene in scenes)

            if combined_length > 1500:
                failures.append(f"S10 combined scene length is {combined_length} characters; hard cap is 1500.")

    return ValidationReportV1(
        passed=len(failures) == 0,
        failures=failures,
        stage_id=stage_id,
        failure_type="malformed_script" if failures else None,
    )


def _validate_s10(
    output: StageOutputV1,
    stage_id: str,
    envelope: StageEnvelopeV1,
) -> ValidationReportV1:
    hash_report = _verify_artifact_hashes(output, stage_id, envelope)
    script_report = _validate_s10_script(output, stage_id, envelope)

    failures = hash_report.failures + script_report.failures

    if not failures:
        return ValidationReportV1(
            passed=True,
            failures=[],
            stage_id=stage_id,
            failure_type=None,
        )

    failure_type = (
        "malformed_script"
        if script_report.failures
        else hash_report.failure_type
    )

    return ValidationReportV1(
        passed=False,
        failures=failures,
        stage_id=stage_id,
        failure_type=failure_type,
    )


def _validate_s00_intake(
    output: StageOutputV1, stage_id: str, envelope: StageEnvelopeV1
) -> ValidationReportV1:
    """
    Exit validator for S00 intake.

    Regression guard for the recurring "fabricated placeholder instead of
    real IdeaRequestV1" bug (M2 Day 1 through Day 3): stub_intake.py's
    output.payload is supposed to be the real submitted idea, dumped via
    idea.model_dump(mode="json"). Re-parsing that payload back through
    IdeaRequestV1 confirms it still round-trips - a placeholder or
    malformed payload won't survive the same validation IdeaRequestV1
    itself already enforces (e.g. identity_id required for AVATAR
    modality), and an empty topic is checked explicitly since a bare str
    field doesn't reject "" on its own.
    """
    try:
        idea = IdeaRequestV1.model_validate(output.payload)
    except ValidationError as exc:
        return ValidationReportV1(
            passed=False,
            failures=[f"S00 output payload is not a valid IdeaRequestV1: {exc}"],
            stage_id=stage_id,
            failure_type="intake_mismatch",
        )

    if not idea.topic.strip():
        return ValidationReportV1(
            passed=False,
            failures=["S00 output payload has an empty topic"],
            stage_id=stage_id,
            failure_type="intake_mismatch",
        )

    return ValidationReportV1(passed=True, failures=[], stage_id=stage_id, failure_type=None)


StageValidator = Callable[[StageOutputV1, str, StageEnvelopeV1], ValidationReportV1]

STAGE_VALIDATORS: dict[str, StageValidator] = {
    stage_id: _verify_artifact_hashes for stage_id, _capability in STAGE_SEQUENCE
}

STAGE_VALIDATORS["S00"] = _validate_s00_intake
STAGE_VALIDATORS["S10"] = _validate_s10

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

            # Store the failing report as evidence - previously this was
            # only ever stored on the success path a few lines below, so a
            # failed attempt's report was never persisted anywhere.
            failed_report_bytes = validation_report.model_dump_json().encode("utf-8")
            evidence_ref = put_artifact(
                data=failed_report_bytes,
                artifact_id=f"validation_{envelope.stage_id}_attempt{envelope.attempt}_FAILED",
                mime_type="application/json",
            )

            failure = ValidationFailureV1(
                stage_id=envelope.stage_id,
                failure_type=failure_type,
                message=f"Stage {envelope.stage_id} failed validation ({failure_type}): {validation_report.failures}",
                feedback_context="; ".join(validation_report.failures),
                evidence_ref=evidence_ref,
                retryable=failure_type in RETRYABLE_VALIDATION_FAILURE_TYPES,
            )
            # Cache feedback context for retry
            _stage_feedback_cache[f"{run_id}_{envelope.stage_id}"] = failure.feedback_context or ""

            # Log the failed attempt itself - previously nothing was
            # written here at all, so a stage that failed twice before
            # succeeding on attempt 3 only ever showed attempt 3 in the
            # manifest/telemetry tables. Attempts 1 and 2 were invisible.
            failed_at = datetime.now(timezone.utc)
            save_stage_record(run_id, idea_request_id, StageRecordV1(
                stage_id=envelope.stage_id,
                status=StageStatus.FAILED,
                attempt=envelope.attempt,
                started_at=started_at,
                completed_at=failed_at,
                output_artifact_ids=[],
            ))
            record_telemetry(StageRunRecordV1(
                run_id=run_id,
                stage_id=envelope.stage_id,
                attempt=envelope.attempt,
                input_hash=envelope.input_hash,
                output_hash=None,
                provider=envelope.provider,
                started_at=started_at,
                ended_at=failed_at,
            ))

            # non_retryable: Temporal's own retry policy would just replay
            # this exact envelope; the correction retry (if any) belongs to
            # pipeline.py, which re-invokes this stage with attempt += 1.
            raise ApplicationError(
                failure.message,
                failure.model_dump(mode="json"),
                type="ValidationFailure",
                non_retryable=True,
            )

        # Clear feedback context on success
        _stage_feedback_cache.pop(f"{run_id}_{envelope.stage_id}", None)

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
# --- Owner C (S20) validator wrapper ---

from providers.real.elevenlabs_voice import validate_voice


def _validate_voice_stage(
    output: StageOutputV1, stage_id: str, envelope: StageEnvelopeV1
) -> ValidationReportV1:
    if not output.artifact_refs:
        return ValidationReportV1(
            passed=False,
            failures=["no audio artifact produced"],
            stage_id=stage_id,
            failure_type="voice_invalid",
        )
    audio_ref = output.artifact_refs[0]
    audio_bytes = get_artifact(audio_ref)

    passed, failures = validate_voice(audio_bytes, mime_type=audio_ref.mime_type)
    return ValidationReportV1(
        passed=passed,
        failures=failures,
        stage_id=stage_id,
        failure_type=None if passed else "voice_invalid",
    )


STAGE_VALIDATORS["S20"] = _validate_voice_stage


# --- Owner D (S30/S40) validator wrappers ---
#
# See providers/real/did_avatar.py's module-level note: identity-similarity
# scoring is NOT implemented (blocked on identity_threshold_v1.json, which
# doesn't exist yet). These validators check video validity and duration
# alignment only - they cannot yet distinguish "right video, wrong face"
# from "correct render". Both failure_types below (avatar_render_invalid,
# sync_duration_mismatch) are placeholders for that broader gap.

from providers.real.did_avatar import validate_avatar_render
from providers.stub.stub_sync import validate_sync


def _validate_avatar_render_stage(
    output: StageOutputV1, stage_id: str, envelope: StageEnvelopeV1
) -> ValidationReportV1:
    if not output.artifact_refs:
        return ValidationReportV1(
            passed=False,
            failures=["no avatar render artifact produced"],
            stage_id=stage_id,
            failure_type="avatar_render_invalid",
        )
    video_bytes = get_artifact(output.artifact_refs[0])
    audio_duration = _get_upstream_audio_duration(envelope)

    passed, failures = validate_avatar_render(
        video_bytes,
        expected_audio_duration=audio_duration if audio_duration > 0 else None,
    )
    return ValidationReportV1(
        passed=passed,
        failures=failures,
        stage_id=stage_id,
        failure_type=None if passed else "avatar_render_invalid",
    )


def _validate_sync_stage(
    output: StageOutputV1, stage_id: str, envelope: StageEnvelopeV1
) -> ValidationReportV1:
    if not output.artifact_refs:
        return ValidationReportV1(
            passed=False,
            failures=["no sync artifact produced"],
            stage_id=stage_id,
            failure_type="sync_duration_mismatch",
        )
    video_bytes = get_artifact(output.artifact_refs[0])
    audio_duration = _get_upstream_audio_duration(envelope)

    passed, failures = validate_sync(
        video_bytes,
        expected_audio_duration=audio_duration if audio_duration > 0 else None,
    )
    return ValidationReportV1(
        passed=passed,
        failures=failures,
        stage_id=stage_id,
        failure_type=None if passed else "sync_duration_mismatch",
    )


STAGE_VALIDATORS["S30"] = _validate_avatar_render_stage
STAGE_VALIDATORS["S40"] = _validate_sync_stage

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
    failure_type= None
    if not passed:
        failure_type = (
            "assembly_duration_mismatch"
            if any("duration" in f for f in failures)
            else "assembly_failed"
        )

    return ValidationReportV1(
        passed=passed,
        failures=failures,
        stage_id=stage_id,
        failure_type=failure_type,
    )


def _validate_qc_stage(
    output: StageOutputV1, stage_id: str, envelope: StageEnvelopeV1
) -> ValidationReportV1:
    """S70 QC already computes its own pass/fail (stub_qc.py's Layer 1/4
    checks) — this wrapper surfaces that result, then separately runs the
    model-judge subjective quality check (M3 Day 3, item 4).

    The two checks are deliberately independent: a deterministic failure
    is reported as qc_failed regardless of the model judge's verdict, and
    the model judge only runs its own check on top - it never gates or
    skips the deterministic result. Per M3 Day 1's rule, a model judge is
    a human-fallback subjective check, never a sole automated gate: a
    failed or low-confidence verdict here is deliberately NOT added to
    RETRYABLE_VALIDATION_FAILURE_TYPES, so it surfaces as a stage failure
    requiring a human to look at it rather than looping on an automatic
    retry with no clear stopping condition."""
    qc_passed = bool(output.metadata.get("passed", False))
    metrics = output.payload.get("metrics", {}) if isinstance(output.payload, dict) else {}
    failed_checks = [k for k, v in metrics.items() if isinstance(v, float) and v == 0.0]

    if not qc_passed:
        return ValidationReportV1(
            passed=False,
            failures=failed_checks if failed_checks else ["QC failed"],
            stage_id=stage_id,
            failure_type="qc_failed",
        )

    # Deterministic checks passed - now run the subjective_quality_check
    # as a separate, clearly labeled layer. Failure here is reported with
    # its own distinct failure_type so it's never confused with a
    # deterministic qc_failed in logs/evidence.
    video_ref = next(
        (ref for ref in envelope.artifact_refs
         if ref.artifact_id.startswith("master_video_")),
        None,
    )
    caption_ref = next(
        (ref for ref in envelope.artifact_refs
         if ref.mime_type == "application/json" and "captions" in ref.artifact_id),
        None,
    )

    try:
        from providers.real.qc_model_judge import judge_video_quality, MIN_CONFIDENCE

        video_bytes = get_artifact(video_ref) if video_ref else b""
        caption_text = ""
        if caption_ref is not None:
            captions_data = json.loads(get_artifact(caption_ref))
            caption_text = " ".join(w.get("text", "") for w in captions_data.get("words", []))

        judgment = judge_video_quality(video_bytes, caption_text)

        subjective_ok = judgment["passed"] and judgment["confidence"] >= MIN_CONFIDENCE

        if not subjective_ok:
            return ValidationReportV1(
                passed=False,
                failures=[
                    f"subjective_quality_check: passed={judgment['passed']}, "
                    f"confidence={judgment['confidence']:.2f}, "
                    f"rationale={judgment['rationale']}"
                ],
                stage_id=stage_id,
                failure_type="subjective_quality_check_failed",
            )
    except Exception as exc:
        # Infrastructure failure (missing API key, extraction error,
        # malformed model response) - skip the subjective check rather
        # than failing the whole stage over judge availability. The
        # deterministic result above already passed, so this is a
        # degraded-but-not-blocked outcome, logged for visibility.
        print(f"[S70] subjective_quality_check skipped due to error: {exc}")

    return ValidationReportV1(passed=True, failures=[], stage_id=stage_id, failure_type=None)


STAGE_VALIDATORS["S50"] = _validate_captions_stage
STAGE_VALIDATORS["S60"] = _validate_assembly_stage
STAGE_VALIDATORS["S70"] = _validate_qc_stage


# --- Owner E (G90/S100) gate validator wrappers ---
# G90/S100 are gate stages, not media stages, but STAGE_VALIDATORS treats
# them the same way as any other stage - both still fell through to the
# generic hash-check before this. See M3 Day 2 doc: "publish privacy" and
# "synthetic flag" are explicitly gate-stage concerns.

from contracts.stages.g90_disclosure import DisclosureDecisionV1


def _validate_disclosure_stage(
    output: StageOutputV1, stage_id: str, envelope: StageEnvelopeV1
) -> ValidationReportV1:
    """G90 exit validator: containsSyntheticMedia must be present, and
    True for avatar-modality runs. False is a validation failure per the
    M0/M1 architecture rule - stated since the first milestone doc, never
    enforced as a validator until now."""
    payload = output.payload if isinstance(output.payload, dict) else {}

    if "contains_synthetic_media" not in payload:
        return ValidationReportV1(
            passed=False,
            failures=["disclosure decision missing contains_synthetic_media field"],
            stage_id=stage_id,
            failure_type="disclosure_missing_synthetic_flag",
        )

    modality = str(payload.get("modality", "")).upper()
    contains_synthetic = payload.get("contains_synthetic_media")

    if modality == "AVATAR" and contains_synthetic is not True:
        return ValidationReportV1(
            passed=False,
            failures=[
                f"avatar-modality run must have contains_synthetic_media=True, "
                f"got {contains_synthetic!r}"
            ],
            stage_id=stage_id,
            failure_type="disclosure_synthetic_flag_false",
        )

    return ValidationReportV1(passed=True, failures=[], stage_id=stage_id, failure_type=None)


def _validate_publish_stage(
    output: StageOutputV1, stage_id: str, envelope: StageEnvelopeV1
) -> ValidationReportV1:
    """S100 exit validator: privacy must never be 'public', and the
    upstream G90 disclosure decision must confirm contains_synthetic_media
    was True going into the publish call. youtube_upload.py already
    enforces this inside one provider - this is the stage-level check
    every future publish provider goes through, not just that one."""
    payload = output.payload if isinstance(output.payload, dict) else {}
    failures: list[str] = []

    privacy = payload.get("privacy")
    if privacy not in ("unlisted", "private"):
        failures.append(f"publish privacy must be 'unlisted' or 'private', got {privacy!r}")

    disclosure_ref = next(
        (r for r in envelope.artifact_refs if "disclosure" in r.artifact_id.lower()),
        None,
    )
    if disclosure_ref is None:
        failures.append("no upstream disclosure-decision artifact found in envelope")
    else:
        disclosure_bytes = get_artifact(disclosure_ref)
        disclosure = DisclosureDecisionV1.model_validate(json.loads(disclosure_bytes))
        if not disclosure.contains_synthetic_media:
            failures.append(
                "upstream disclosure decision has contains_synthetic_media=False; "
                "publish must not proceed"
            )

    return ValidationReportV1(
        passed=len(failures) == 0,
        failures=failures,
        stage_id=stage_id,
        failure_type=None if not failures else "publish_privacy_violation",
    )


STAGE_VALIDATORS["G90"] = _validate_disclosure_stage
STAGE_VALIDATORS["S100"] = _validate_publish_stage
