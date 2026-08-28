import json
import hashlib
from unittest.mock import patch, MagicMock

import pytest

from contracts.common.envelope import (
    ArtifactRefV1,
    ProviderDescriptorV1,
    StageEnvelopeV1,
    StageOutputV1,
    ValidationReportV1,
)
from contracts.stages.idea_request import Modality, IdeaRequestV1
from contracts.stages.g90_disclosure import DisclosureDecisionV1
from orchestrator.stage_executor import STAGE_VALIDATORS
from providers.stub.stub_disclosure import StubDisclosureProvider


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


# Set up patches to run all validators successfully offline/without database or third-party service connections.
@patch("orchestrator.stage_executor.get_artifact")
@patch("orchestrator.stage_executor.validate_voice")
@patch("orchestrator.stage_executor.compute_speaker_similarity")
@patch("orchestrator.stage_executor.validate_avatar_render")
@patch("orchestrator.stage_executor.compute_identity_similarity")
@patch("orchestrator.stage_executor.validate_sync")
@patch("orchestrator.stage_executor.validate_captions")
@patch("orchestrator.stage_executor.validate_assembly")
@patch("orchestrator.stage_executor._measure_duration")
@patch("providers.real.qc_model_judge.judge_video_quality")
@patch("orchestrator.stage_executor._fetch_reference_voice_bytes")
def test_full_validator_chain_dry_run_faceless(
    mock_fetch_voice,
    mock_judge_video,
    mock_measure_dur,
    mock_val_assembly,
    mock_val_captions,
    mock_val_sync,
    mock_comp_identity,
    mock_val_avatar,
    mock_comp_speaker,
    mock_val_voice,
    mock_get_artifact,
):
    # Configure mock return values
    mock_get_artifact.return_value = b"{}"
    mock_val_voice.return_value = (True, [])
    mock_comp_speaker.return_value = 0.95
    mock_val_avatar.return_value = (True, [])
    mock_comp_identity.return_value = 0.95
    mock_val_sync.return_value = (True, [])
    mock_val_captions.return_value = (True, [])
    mock_val_assembly.return_value = (True, [])
    mock_measure_dur.return_value = 5.0
    mock_judge_video.return_value = {"passed": True, "confidence": 0.95, "rationale": "Looks good"}
    mock_fetch_voice.return_value = b"voice_reference_samples"

    # Walk S00 to S100 in order
    
    # ------------------ S00: Intake ------------------
    idea_payload = {
        "idea_request_id": "test_run_faceless",
        "modality": "faceless",
        "topic": "neural networks explainer",
        "voice_id": "voice_002",
    }
    s00_output = StageOutputV1(
        payload=idea_payload,
        metadata={},
        artifact_refs=[],
    )
    s00_envelope = _make_envelope("S00", "intake")
    s00_report = STAGE_VALIDATORS["S00"](s00_output, "S00", s00_envelope)
    assert s00_report.passed is True, f"S00 failed: {s00_report.failures}"

    # ------------------ S10: Script ------------------
    script_payload = {
        "run_id": "test_run_faceless",
        "scenes": ["Scene 1 script here.", "Scene 2 script here.", "Scene 3 script here."],
    }
    s10_output = StageOutputV1(
        payload=script_payload,
        metadata={},
        artifact_refs=[],
    )
    s10_envelope = _make_envelope("S10", "script_generation")
    s10_report = STAGE_VALIDATORS["S10"](s10_output, "S10", s10_envelope)
    assert s10_report.passed is True, f"S10 failed: {s10_report.failures}"

    # ------------------ S20: Voice ------------------
    voice_payload = {
        "voice_id": "voice_002",
        "narration_text": "Scene 1 script here. Scene 2 script here. Scene 3 script here.",
    }
    voice_artifact = ArtifactRefV1(
        artifact_id="audio_test_run_faceless",
        path="s3://bucket/artifacts/audio_test_run_faceless.wav",
        hash=hashlib.sha256(b"audio").hexdigest(),
        mime_type="audio/wav",
    )
    s20_output = StageOutputV1(
        payload=voice_payload,
        metadata={},
        artifact_refs=[voice_artifact],
    )
    s20_envelope = _make_envelope("S20", "voice_synthesis")
    
    # Mock get_artifact to return different bytes depending on what artifact is requested
    mock_get_artifact.side_effect = lambda ref: b"audio" if ref == voice_artifact else b"{}"

    s20_report = STAGE_VALIDATORS["S20"](s20_output, "S20", s20_envelope)
    assert s20_report.passed is True, f"S20 failed: {s20_report.failures}"

    # ------------------ S30: Avatar Render (Faceless modality - skipped identity check) ------------------
    video_artifact = ArtifactRefV1(
        artifact_id="video_test_run_faceless",
        path="s3://bucket/artifacts/video_test_run_faceless.mp4",
        hash=hashlib.sha256(b"video").hexdigest(),
        mime_type="video/mp4",
    )
    s30_output = StageOutputV1(
        payload={},
        metadata={},
        artifact_refs=[video_artifact],
    )
    # Important: Do NOT include an identity_ref in the envelope's artifact_refs.
    s30_envelope = _make_envelope("S30", "avatar_render", artifact_refs=[voice_artifact])
    
    # Set up mock side effects for artifact retrieval
    def get_artifact_side_effect(ref):
        if ref == video_artifact:
            return b"video"
        elif ref == voice_artifact:
            return b"audio"
        return b"{}"
    mock_get_artifact.side_effect = get_artifact_side_effect

    # S30 must skip identity check for faceless and print the S30 validator skip message.
    s30_report = STAGE_VALIDATORS["S30"](s30_output, "S30", s30_envelope)
    assert s30_report.passed is True, f"S30 failed: {s30_report.failures}"

    # ------------------ S40: Sync ------------------
    s40_output = StageOutputV1(
        payload={},
        metadata={},
        artifact_refs=[video_artifact],
    )
    s40_envelope = _make_envelope("S40", "sync_check", artifact_refs=[voice_artifact])
    s40_report = STAGE_VALIDATORS["S40"](s40_output, "S40", s40_envelope)
    assert s40_report.passed is True, f"S40 failed: {s40_report.failures}"

    # ------------------ S50: Captions ------------------
    captions_data = {
        "words": [{"text": "hello", "start": 0.0, "end": 1.0}]
    }
    captions_bytes = json.dumps(captions_data).encode("utf-8")
    captions_artifact = ArtifactRefV1(
        artifact_id="captions_test_run_faceless",
        path="s3://bucket/artifacts/captions_test_run_faceless.json",
        hash=hashlib.sha256(captions_bytes).hexdigest(),
        mime_type="application/json",
    )
    s50_output = StageOutputV1(
        payload=captions_data,
        metadata={},
        artifact_refs=[captions_artifact],
    )
    s50_envelope = _make_envelope("S50", "caption_generation", artifact_refs=[voice_artifact])
    
    def get_artifact_side_effect_s50(ref):
        if ref == captions_artifact:
            return captions_bytes
        elif ref == voice_artifact:
            return b"audio"
        return b"{}"
    mock_get_artifact.side_effect = get_artifact_side_effect_s50

    s50_report = STAGE_VALIDATORS["S50"](s50_output, "S50", s50_envelope)
    assert s50_report.passed is True, f"S50 failed: {s50_report.failures}"

    # ------------------ S60: Assembly ------------------
    s60_output = StageOutputV1(
        payload={},
        metadata={},
        artifact_refs=[video_artifact],
    )
    s60_envelope = _make_envelope("S60", "assembly", artifact_refs=[voice_artifact])
    
    def get_artifact_side_effect_s60(ref):
        if ref == video_artifact:
            return b"video"
        elif ref == voice_artifact:
            return b"audio"
        return b"{}"
    mock_get_artifact.side_effect = get_artifact_side_effect_s60

    s60_report = STAGE_VALIDATORS["S60"](s60_output, "S60", s60_envelope)
    assert s60_report.passed is True, f"S60 failed: {s60_report.failures}"

    # ------------------ S70: QC ------------------
    s70_output = StageOutputV1(
        payload={"metrics": {"framerate": 29.97, "duration": 5.0}},
        metadata={"passed": True},
        artifact_refs=[video_artifact],
    )
    s70_envelope = _make_envelope("S70", "quality_control", artifact_refs=[video_artifact, captions_artifact])
    
    def get_artifact_side_effect_s70(ref):
        if ref == video_artifact:
            return b"video"
        elif ref == captions_artifact:
            return captions_bytes
        return b"{}"
    mock_get_artifact.side_effect = get_artifact_side_effect_s70

    s70_report = STAGE_VALIDATORS["S70"](s70_output, "S70", s70_envelope)
    assert s70_report.passed is True, f"S70 failed: {s70_report.failures}"

    # ------------------ G90: Disclosure ------------------
    # Verify passed=True for modality=faceless, contains_synthetic_media=True
    disclosure_valid_payload = {
        "modality": "faceless",
        "master_video_hash": "e" * 64,
        "contains_synthetic_media": True,
        "policy_basis": "policy_stub_g90",
    }
    g90_output_valid = StageOutputV1(
        payload=disclosure_valid_payload,
        metadata={},
        artifact_refs=[],
    )
    g90_envelope = _make_envelope("G90", "disclosure_check")
    g90_report_valid = STAGE_VALIDATORS["G90"](g90_output_valid, "G90", g90_envelope)
    assert g90_report_valid.passed is True

    # Verify passed=False for modality=faceless, contains_synthetic_media=False
    disclosure_invalid_payload = {
        "modality": "faceless",
        "master_video_hash": "e" * 64,
        "contains_synthetic_media": False,
        "policy_basis": "policy_stub_g90",
    }
    g90_output_invalid = StageOutputV1(
        payload=disclosure_invalid_payload,
        metadata={},
        artifact_refs=[],
    )
    g90_report_invalid = STAGE_VALIDATORS["G90"](g90_output_invalid, "G90", g90_envelope)
    assert g90_report_invalid.passed is False
    assert g90_report_invalid.failure_type == "disclosure_synthetic_flag_false"

    # ------------------ S100: Publish ------------------
    disclosure_decision = DisclosureDecisionV1(
        modality=Modality.FACELESS,
        master_video_hash="e" * 64,
        contains_synthetic_media=True,
        policy_basis="policy_stub_g90",
    )
    disclosure_bytes = disclosure_decision.model_dump_json().encode("utf-8")
    disclosure_artifact = ArtifactRefV1(
        artifact_id="disclosure_test_run_faceless",
        path="s3://bucket/artifacts/disclosure_test_run_faceless.json",
        hash=hashlib.sha256(disclosure_bytes).hexdigest(),
        mime_type="application/json",
    )

    s100_output = StageOutputV1(
        payload={"privacy": "unlisted"},
        metadata={},
        artifact_refs=[],
    )
    s100_envelope = _make_envelope("S100", "publish", artifact_refs=[disclosure_artifact])
    
    mock_get_artifact.side_effect = lambda ref: disclosure_bytes if ref == disclosure_artifact else b"{}"

    s100_report = STAGE_VALIDATORS["S100"](s100_output, "S100", s100_envelope)
    assert s100_report.passed is True, f"S100 failed: {s100_report.failures}"


# Integration test verifying that the dynamic modality stub disclosure fix integrates cleanly with the validator fix.
@patch("providers.stub.stub_disclosure.put_artifact")
@patch("providers.stub.stub_disclosure.get_artifact")
def test_g90_provider_validator_integration_faceless(mock_get_artifact, mock_put_artifact):
    # Mock S3 calls for the provider execution
    mock_put_artifact.side_effect = lambda data, artifact_id, mime_type: ArtifactRefV1(
        artifact_id=artifact_id,
        path=f"s3://bucket/artifacts/{artifact_id}",
        hash=hashlib.sha256(data).hexdigest(),
        mime_type=mime_type,
    )
    
    # 1. Mock the idea request artifact with modality = "faceless"
    idea_ref = ArtifactRefV1(
        artifact_id="idea_test_run_faceless",
        path="s3://bucket/artifacts/idea_test_run_faceless",
        hash="d" * 64,
        mime_type="application/json",
    )
    idea_data = {
        "idea_request_id": "test_run_faceless",
        "modality": "faceless",
        "topic": "test topic",
        "voice_id": "voice_002",
    }
    
    video_ref = ArtifactRefV1(
        artifact_id="master_video_test",
        path="s3://bucket/artifacts/master_video_test.mp4",
        hash="e" * 64,
        mime_type="video/mp4",
    )

    mock_get_artifact.return_value = json.dumps(idea_data).encode("utf-8")

    # 2. Run StubDisclosureProvider.run() and confirm it emits modality=faceless
    provider = StubDisclosureProvider()
    envelope = StageEnvelopeV1(
        stage_id="G90",
        attempt=1,
        input_hash="c" * 64,
        artifact_refs=[video_ref, idea_ref],
        validation_ref=None,
        provider=ProviderDescriptorV1(
            provider="stub_disclosure_provider",
            model="stub-v1",
            version="1.0.0",
            capability="disclosure_check",
        ),
    )

    output = provider.run(envelope, "test_run_faceless")
    
    # Ensure it emitted modality=faceless
    assert output.payload["modality"] == Modality.FACELESS
    assert output.payload["contains_synthetic_media"] is True

    # 3. Feed output to STAGE_VALIDATORS["G90"] and confirm passed=True
    report_valid = STAGE_VALIDATORS["G90"](output, "G90", envelope)
    assert report_valid.passed is True, f"G90 validator rejected valid faceless output: {report_valid.failures}"

    # 4. Mutate contains_synthetic_media to False and confirm passed=False
    output.payload["contains_synthetic_media"] = False
    report_invalid = STAGE_VALIDATORS["G90"](output, "G90", envelope)
    assert report_invalid.passed is False
    assert report_invalid.failure_type == "disclosure_synthetic_flag_false"
