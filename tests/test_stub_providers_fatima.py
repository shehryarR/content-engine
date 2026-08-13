"""
tests/test_stub_providers.py

Basic integration tests for the Day 2 stub providers.

These tests verify that each provider:
- accepts a StageEnvelopeV1
- returns a StageOutputV1
- stores a real artifact through storage.py
- returns a valid ArtifactRefV1
"""

import hashlib
import json

from contracts.common.envelope import (
    ProviderDescriptorV1,
    StageEnvelopeV1,
)
from orchestrator.storage import get_artifact

from providers.stub.stub_intake import StubIntakeProvider
from providers.stub.stub_captions import StubCaptionsProvider
from providers.real.assembly import AssemblyProvider
from contracts.stages.idea_request import IdeaRequestV1, Modality


def make_envelope(stage_id: str, capability: str) -> StageEnvelopeV1:
    payload = {
        "run_id": "test_run_001",
    }

    return StageEnvelopeV1(
        stage_id=stage_id,
        attempt=1,
        input_hash=hashlib.sha256(
            json.dumps(payload, sort_keys=True).encode()
        ).hexdigest(),
        artifact_refs=[],
        validation_ref=None,
        provider=ProviderDescriptorV1(
            provider=capability,
            model="stub",
            version="0.1.0",
            capability=capability,
        ),
    )


def test_stub_intake():
    provider = StubIntakeProvider()

    idea = IdeaRequestV1(
        idea_request_id="test_run_001",
        modality=Modality.AVATAR,
        topic="some distinctive test topic",
        identity_id="identity_001",
        voice_id="voice_001",
    )

    output = provider.run(
        make_envelope("S00", provider.capability),
        "test_run_001",
        idea.model_dump(mode="json"),
    )

    assert len(output.artifact_refs) == 1

    artifact = output.artifact_refs[0]

    stored = get_artifact(artifact)

    assert stored
    assert output.payload["idea_request_id"] == "test_run_001"
    assert output.payload["topic"] == "some distinctive test topic"

def test_stub_captions():
    provider = StubCaptionsProvider()

    output = provider.run(
        make_envelope("S50", provider.capability),"test_run_001"
    )

    assert len(output.artifact_refs) == 1

    artifact = output.artifact_refs[0]

    stored = get_artifact(artifact)

    assert stored
    assert output.payload["word_count"] == 4
    assert output.payload["run_id"] == "test_run_001" 


def test_stub_assembly():
    provider = AssemblyProvider()

    output = provider.run(
        make_envelope("S60", provider.capability), "test_run_001"
    )

    assert len(output.artifact_refs) == 1

    artifact = output.artifact_refs[0]

    stored = get_artifact(artifact)

    assert stored
    assert output.payload["scene_count"] == 3
    assert output.payload["duration_seconds"] == 5.0
    assert output.payload["run_id"] == "test_run_001"