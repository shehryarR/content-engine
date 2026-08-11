"""
orchestrator/pipeline.py

The real 11-stage pipeline workflow. Replaces hello_workflow as the
primary workflow the worker runs.
"""

from datetime import timedelta

from temporalio import workflow

with workflow.unsafe.imports_passed_through():
    import hashlib
    import json
    from contracts.common.envelope import (
        ProviderDescriptorV1,
        StageEnvelopeV1,
        ArtifactRefV1,
    )
    from contracts.stages.g80_approval import HumanApprovalV1
    from graph.pipeline_graph import STAGE_SEQUENCE
import asyncio

TASK_QUEUE = "avatar-harness"
STAGE_TIMEOUT = timedelta(minutes=5)



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

@workflow.defn
class AvatarPipeline:
    """
    The full S00-to-S100 pipeline. Processing stages are dispatched
    via the generic run_stage Activity. G80 (approval) is a durable
    signal wait that pauses the workflow until a human sends a signal.
    """

    def __init__(self):
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
        envelope_dict = _build_envelope("S00", "intake", run_id, artifact_refs=[])
        output_dict = await workflow.execute_activity(
            "run_intake_stage",
            args=[idea, run_id, envelope_dict],
            start_to_close_timeout=STAGE_TIMEOUT,
        )
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

            envelope_dict = _build_envelope(
                stage_id,
                capability,
                run_id,
                artifact_refs=prior_artifact_refs,
                validation_ref=last_validation_ref
            )
            output_dict = await workflow.execute_activity(
                "run_stage",
                args=[capability, envelope_dict, run_id, run_id],
                start_to_close_timeout=STAGE_TIMEOUT,
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
            args=[run_id, run_id, g80_started_at.isoformat(), workflow.now().isoformat()],
            start_to_close_timeout=STAGE_TIMEOUT,
        )

        # --- G90 + S100: disclosure check then publish ---
        for stage_id, capability in STAGE_SEQUENCE[8:]:
            prior_artifact_refs = []
            for prior_output in self._stage_outputs.values():
                prior_artifact_refs.extend(prior_output.get("artifact_refs", []))

            envelope_dict = _build_envelope(
                stage_id,
                capability,
                run_id,
                artifact_refs=prior_artifact_refs,
                validation_ref=last_validation_ref,
            )
            output_dict = await workflow.execute_activity(
                "run_stage",
                args=[capability, envelope_dict, run_id, run_id],
                start_to_close_timeout=STAGE_TIMEOUT,
            )
            last_validation_ref = output_dict.pop("_validation_ref", None)
            self._stage_outputs[stage_id] = output_dict
            workflow.logger.info(f"Stage {stage_id} completed")

        return {
            "G90": self._stage_outputs.get("G90", {}),
            "S100": self._stage_outputs.get("S100", {}),
        }