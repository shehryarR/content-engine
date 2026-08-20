"""
orchestrator/pipeline.py

The real 11-stage pipeline workflow. Replaces hello_workflow as the
primary workflow the worker runs.
"""

from datetime import timedelta

from temporalio import workflow
from temporalio.exceptions import ActivityError, ApplicationError

with workflow.unsafe.imports_passed_through():
    import hashlib
    import json
    from contracts.common.envelope import (
        ProviderDescriptorV1,
        StageEnvelopeV1,
        ArtifactRefV1,
    )
    from contracts.common.correction_plan import CorrectionPlanV1, DEFAULT_RETRY_BUDGET
    from contracts.common.validation_failure import ValidationFailureV1
    from contracts.stages.g80_approval import HumanApprovalV1
    from graph.pipeline_graph import STAGE_SEQUENCE
import asyncio

TASK_QUEUE = "avatar-harness"
STAGE_TIMEOUT = timedelta(minutes=5)

# Failure types a local correction retry can plausibly fix. Anything else
# (e.g. a provider outage) isn't worth re-running the same stage for.
RETRYABLE_VALIDATION_FAILURE_TYPES = {
    "hash_mismatch",
    # S60 assembly failures are often transient (temp-file cleanup race,
    # ffmpeg resource contention) — a same-inputs retry is worth it.
    "assembly_failed",
    "assembly_duration_mismatch",
    # S50 caption timing is deterministic given the same audio, so a
    # blind resubmit reproduces the same failure - BUT the M3 Day 1
    # success criteria explicitly requires this fixture to trigger a
    # local retry with "a documented retry strategy distinct from a
    # plain resubmit". Included as retryable; the actual Whisper call
    # itself doesn't change, but retrying still re-runs S50 in isolation
    # without touching S00-S40, which is the behavior being tested here.
    # Owner E decision, M3 Day 1 Part 5.
    "caption_timing_invalid",
    # S70 QC re-checks the same S60 video - retrying re-validates nothing
    # new, so NOT included.
    "malformed_script",
    "voice_invalid",
    "avatar_render_invalid",
    "sync_duration_mismatch",
    # S00 intake_mismatch: a cheap, safe retry - re-runs run_intake_stage
    # with the same real idea_dict, no external call involved.
    "intake_mismatch",
}



def _build_envelope(
    stage_id: str,
    capability: str,
    run_id: str,
    artifact_refs: list[dict] | None = None,
    attempt: int = 1,
    validation_ref: dict | None = None,   # ADD THIS
) -> dict:
    input_data = {"run_id": run_id, "stage_id": stage_id}
    input_hash = hashlib.sha256(
        json.dumps(input_data, sort_keys=True).encode()
    ).hexdigest()

    envelope = StageEnvelopeV1(
        stage_id=stage_id,
        attempt=attempt,
        input_hash=input_hash,
        artifact_refs=artifact_refs or [],
        validation_ref=ArtifactRefV1.model_validate(validation_ref) if validation_ref else None,  # ADD THIS
        provider=ProviderDescriptorV1(
            provider=capability,
            model="stub",
            version="0.1.0",
            capability=capability,
        ),
    )
    return envelope.model_dump()


async def _run_stage_with_correction(
    stage_id: str,
    capability: str,
    run_id: str,
    artifact_refs: list[dict],
    validation_ref: dict | None,
    identity_id: str | None = None,
) -> dict:
    """
    Run one stage via the generic run_stage Activity, retrying that same
    stage_id locally (attempt += 1, upstream artifact_refs left untouched)
    when the activity fails with a ValidationFailureV1. Any other activity
    failure (timeouts, worker crashes, non-validation errors) is re-raised
    and left to Temporal's own retry policy / workflow failure handling.

    S30-specific retry semantics (M3 Day 1 Part 4): an avatar_render
    failure most likely means the wrong reference_asset was fetched from
    the registry, not that D-ID "got it wrong". So on a retryable S30
    failure, re-fetch the identity reference from identity_profiles via
    fetch_identity_reference fresh before re-calling D-ID, instead of
    blindly resubmitting the same (possibly still-wrong) artifact_refs.
    Every other stage keeps re-submitting the same artifact_refs across
    attempts, unchanged.
    """
    attempt = 1
    current_artifact_refs = artifact_refs
    while True:
        envelope_dict = _build_envelope(
            stage_id,
            capability,
            run_id,
            artifact_refs=current_artifact_refs,
            attempt=attempt,
            validation_ref=validation_ref,
        )
        try:
            return await workflow.execute_activity(
                "run_stage",
                args=[capability, envelope_dict, run_id, run_id],
                start_to_close_timeout=STAGE_TIMEOUT,
            )
        except ActivityError as exc:
            cause = exc.cause
            if not isinstance(cause, ApplicationError) or cause.type != "ValidationFailure":
                raise
            failure = ValidationFailureV1.model_validate(cause.details[0])
            plan = CorrectionPlanV1(
                target_stage=stage_id,
                retryable=(
                    failure.failure_type in RETRYABLE_VALIDATION_FAILURE_TYPES
                    and attempt < DEFAULT_RETRY_BUDGET
                ),
                feedback_context=failure.feedback_context,
            )
            if not plan.retryable:
                raise

            if stage_id == "S30" and identity_id:
                workflow.logger.warning(
                    f"Stage S30 attempt {attempt} failed ({failure.failure_type}): "
                    f"{failure.message}. Re-fetching identity reference "
                    f"{identity_id} from the registry before retrying, rather "
                    f"than resubmitting the same reference."
                )
                fresh_identity_ref = await workflow.execute_activity(
                    "fetch_identity_reference",
                    args=[identity_id],
                    start_to_close_timeout=STAGE_TIMEOUT,
                )
                # Re-validate consent on the fresh reference before resubmitting
                # to D-ID. validate_run only ran once, pre-flight - consent can
                # be revoked mid-run, and a retry must not silently resubmit a
                # reference whose consent grant no longer holds. Raises (via
                # ApplicationError) and is intentionally left uncaught here:
                # a revoked-consent failure is a hard stop, not something this
                # retry loop should absorb.
                await workflow.execute_activity(
                    "revalidate_identity_consent",
                    args=[identity_id],
                    start_to_close_timeout=STAGE_TIMEOUT,
                )
                current_artifact_refs = [
                    ref for ref in current_artifact_refs
                    if not str(ref.get("artifact_id", "")).startswith("identity_ref_")
                ] + [fresh_identity_ref]
            else:
                workflow.logger.warning(
                    f"Stage {stage_id} attempt {attempt} failed validation "
                    f"({failure.failure_type}): {failure.message}. Retrying "
                    f"(budget {plan.retry_budget})."
                )
            attempt += 1


async def _run_intake_with_correction(idea: dict, run_id: str) -> dict:
    """
    S00 equivalent of _run_stage_with_correction. Can't reuse that helper
    directly - run_intake_stage's activity signature (idea_dict, run_id,
    envelope_dict) differs from every other stage's (capability,
    envelope_dict, run_id, run_id), and S00 has no upstream artifact_refs
    to carry across attempts.

    Before this, S00 validation failures were never retried at all: the
    workflow called run_intake_stage as a plain workflow.execute_activity
    with no try/except around it, so a ValidationFailure raised inside
    the activity propagated straight up and killed the whole workflow run
    instead of retrying locally the way S10-S70/G90/S100 do.
    """
    attempt = 1
    while True:
        envelope_dict = _build_envelope(
            "S00", "intake", run_id, artifact_refs=[], attempt=attempt
        )
        try:
            return await workflow.execute_activity(
                "run_intake_stage",
                args=[idea, run_id, envelope_dict],
                start_to_close_timeout=STAGE_TIMEOUT,
            )
        except ActivityError as exc:
            cause = exc.cause
            if not isinstance(cause, ApplicationError) or cause.type != "ValidationFailure":
                raise
            failure = ValidationFailureV1.model_validate(cause.details[0])
            plan = CorrectionPlanV1(
                target_stage="S00",
                retryable=(
                    failure.failure_type in RETRYABLE_VALIDATION_FAILURE_TYPES
                    and attempt < DEFAULT_RETRY_BUDGET
                ),
                feedback_context=failure.feedback_context,
            )
            if not plan.retryable:
                raise
            workflow.logger.warning(
                f"Stage S00 attempt {attempt} failed validation "
                f"({failure.failure_type}): {failure.message}. Retrying "
                f"(budget {plan.retry_budget})."
            )
            attempt += 1


@workflow.defn
class AvatarPipeline:
    """
    The full S00-to-S100 pipeline. Processing stages are dispatched
    via the generic run_stage Activity. G80 (approval) is a durable
    signal wait that pauses the workflow until a human sends a signal.
    """

    def _init_(self):
        self._approval: dict | None = None
        self._stage_outputs: dict[str, dict] = {}
        self._current_master_hash: str | None = None

    @workflow.signal
    async def approve(self, decision: dict) -> None:
        """
        Receive an approval signal from an external tool (e.g. scripts/approve.py).
        The decision dict should be a HumanApprovalV1.model_dump().
        """
        # Hardened G80 gate: check master_video_hash matches current assembly output
        decision_hash = decision.get("master_video_hash")
        if self._current_master_hash is None or decision_hash == self._current_master_hash:
            self._approval = decision
        else:
            workflow.logger.warning(
                f"Ignoring stale approval signal for hash {decision_hash}. "
                f"Expected current master hash: {self._current_master_hash}"
            )

    @workflow.run
    async def run(self, idea_json: str) -> dict:
        """
        Execute the full pipeline for one IdeaRequestV1.

        Args:
            idea_json: JSON string of the IdeaRequestV1.

        Returns:
            The final stage's StageOutputV1 as a dict.
        """
        idea = json.loads(idea_json)
        if "idea_request_id" not in idea:
            raise ValueError("idea_request_id field is missing from idea payload")
        run_id = idea.get("idea_request_id")
        if not run_id:
            raise ValueError("idea_request_id field is missing from idea payload")
        last_validation_ref: dict | None = None
        output_dict = await _run_intake_with_correction(idea, run_id)
        last_validation_ref = output_dict.pop("_validation_ref", None)
        self._stage_outputs["S00"] = output_dict

        # Identity reference isn't a pipeline stage output — it's a registry-seeded
        # artifact — so it has to be fetched explicitly here rather than arriving
        # through the normal prior-stage accumulation. Case-insensitive compare
        # since modality strings have appeared as both "avatar" and "AVATAR"
        # across config files vs the enum.
        identity_ref_dict = None
        modality_value = str(idea.get("modality", "")).upper()
        if modality_value == "AVATAR" and idea.get("identity_id"):
            identity_ref_dict = await workflow.execute_activity(
                "fetch_identity_reference",
                args=[idea["identity_id"]],
                start_to_close_timeout=STAGE_TIMEOUT,
            )
        if identity_ref_dict is not None:
            self._stage_outputs["_identity_ref"] = {"artifact_refs": [identity_ref_dict]}

        # STAGE_SEQUENCE[1:] then runs through the existing loop, unchanged
        for stage_id, capability in STAGE_SEQUENCE[1:8]:
            prior_artifact_refs: list[dict] = []
            for prior_output in self._stage_outputs.values():
                prior_artifact_refs.extend(prior_output.get("artifact_refs", []))

            output_dict = await _run_stage_with_correction(
                stage_id,
                capability,
                run_id,
                prior_artifact_refs,
                last_validation_ref,
                identity_id=idea.get("identity_id"),
            )
            last_validation_ref = output_dict.pop("_validation_ref", None)
            self._stage_outputs[stage_id] = output_dict

            # Record current master video hash from S60 assembly output if available
            if stage_id == "S60":
                refs = output_dict.get("artifact_refs", [])
                if refs and isinstance(refs, list) and "hash" in refs[0]:
                    self._current_master_hash = refs[0]["hash"]
                elif "payload" in output_dict and isinstance(output_dict["payload"], dict):
                    video_art = output_dict["payload"].get("video_artifact", {})
                    self._current_master_hash = video_art.get("hash")

            workflow.logger.info(f"Stage {stage_id} completed")

        # --- G80: durable wait for human approval ---
        workflow.logger.info("Waiting for approval signal (G80)...")
        g80_started_at = workflow.now()
        try:
            await workflow.wait_condition(lambda: self._approval is not None, timeout=timedelta(minutes=30))
        except asyncio.TimeoutError:
            workflow.logger.error("G80 approval timed out after 30 minutes - no valid signal received")
            raise

        workflow.logger.info(f"Approval received: {self._approval}")

        await workflow.execute_activity(
            "record_g80_approval",
            args=[
                run_id,
                run_id,
                g80_started_at.isoformat(),
                workflow.now().isoformat(),
                self._approval,
            ],
            start_to_close_timeout=STAGE_TIMEOUT,
        )

        # --- G90 + S100: disclosure check then publish ---
        for stage_id, capability in STAGE_SEQUENCE[8:]:
            prior_artifact_refs = []
            for prior_output in self._stage_outputs.values():
                prior_artifact_refs.extend(prior_output.get("artifact_refs", []))

            output_dict = await _run_stage_with_correction(
                stage_id,
                capability,
                run_id,
                prior_artifact_refs,
                last_validation_ref,
            )
            last_validation_ref = output_dict.pop("_validation_ref", None)
            self._stage_outputs[stage_id] = output_dict
            workflow.logger.info(f"Stage {stage_id} completed")

        return {
            "G90": self._stage_outputs.get("G90", {}),
            "S100": self._stage_outputs.get("S100", {}),
        }