"""
providers/stub_script.py

S10 Script Generation Stub Provider.

Produces a deterministic ScriptPackageV1 payload wrapped inside a StageOutputV1,
and persists the script as a real artifact via storage.py.
"""

import json

from contracts.common.envelope import StageEnvelopeV1, StageOutputV1
from contracts.stages.s10_script import ScriptPackageV1
from orchestrator.storage import put_artifact


class StubScriptProvider:
    """
    Stub implementation for S10 script generation capability.

    Always returns a deterministic ScriptPackageV1 payload matching the input run_id or default.
    """

    capability: str = "script_generation"

    def run(self, envelope: StageEnvelopeV1, run_id: str) -> StageOutputV1:
        

        script_package = ScriptPackageV1(
            run_id=run_id,
            scenes=[
                "Welcome to this AI avatar demonstration.",
                "In this video, we explore how deterministic pipelines ensure reliable automated publishing.",
                "Thank you for watching!",
            ],
        )

        script_bytes = json.dumps(script_package.model_dump()).encode("utf-8")
        artifact_ref = put_artifact(
            data=script_bytes,
            artifact_id=f"script_{run_id}",
            mime_type="application/json",
        )

        return StageOutputV1(
            payload=script_package.model_dump(),
            metadata={
                "stub": True,
                "provider": "stub_script_provider",
                "scene_count": len(script_package.scenes),
            },
            artifact_refs=[artifact_ref],
        )
