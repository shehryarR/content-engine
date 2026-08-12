from datetime import UTC, datetime

"""
providers/stub_intake.py

S00 Intake Stub Provider.

Records the incoming IdeaRequestV1 as the pipeline's first tracked artifact
and returns it wrapped inside a StageOutputV1.

Design note: StageEnvelopeV1 does not carry the original IdeaRequestV1 payload, and
run_id now arrives as an explicit parameter (per the StageProvider interface change),
not through envelope.payload (which was never a real field to begin with).

Known follow-up, not fixed in this pass: modality/topic/identity_id/voice_id below are
still fabricated placeholders rather than the real values from the IdeaRequestV1 the
CLI actually submitted. run_id is correct now; the rest of the request still isn't
threaded through to this stage. Left as a deliberate follow-up rather than bundled into
this fix, since it needs a small design decision (how the real idea payload reaches S00
specifically) rather than a one-line change like the rest of this fix.
"""

import json

from contracts.common.envelope import StageEnvelopeV1, StageOutputV1
from contracts.stages.idea_request import IdeaRequestV1, Modality
from orchestrator.storage import put_artifact


class StubIntakeProvider:
    """Stub implementation for S00 intake capability."""

    capability: str = "intake"

    def run(self, envelope: StageEnvelopeV1, run_id: str,idea_dict:dict) -> StageOutputV1:
        idea = IdeaRequestV1.model_validate(idea_dict)

        idea_bytes = json.dumps(
            idea.model_dump(mode="json")
        ).encode("utf-8")

        artifact = put_artifact(
            data=idea_bytes,
            artifact_id=f"idea_{run_id}",
            mime_type="application/json",
        )


        return StageOutputV1(
            payload=idea.model_dump(mode="json"),
            metadata={
                "stub": True,
                "provider": "stub_intake_provider",
            },
            artifact_refs=[artifact],
        )