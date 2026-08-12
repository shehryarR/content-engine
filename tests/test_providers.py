"""
tests/test_providers.py

Tests for all stage providers.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from contracts.common.envelope import (
    ArtifactRefV1,
    ProviderDescriptorV1,
    StageEnvelopeV1,
    StageOutputV1,
)
from contracts.stages.g90_disclosure import DisclosureDecisionV1
from contracts.stages.s10_script import ScriptPackageV1
from contracts.stages.s20_voice import VoiceTrackV1
from contracts.stages.s30_avatar import PrimaryVisualTrackV1
from contracts.stages.s40_sync import SynchronizedMediaV1
from contracts.stages.s70_qc import QualityReportV1
from orchestrator.registry import clear, get, register
from providers.base import StageProvider
from providers.stub.stub_avatar import StubAvatarProvider
from providers.stub.stub_disclosure import StubDisclosureProvider
from providers.stub.stub_qc import StubQCProvider
from providers.stub.stub_script import StubScriptProvider
from providers.stub.stub_sync import StubSyncProvider
from providers.stub.stub_voice import StubVoiceProvider

@pytest.fixture(autouse=True)
def clear_registry():
    """Clear the registry before and after each test."""
    clear()
    yield
    clear()


@pytest.fixture
def voice_provider():
    return StubVoiceProvider()


@pytest.fixture
def avatar_provider():
    return StubAvatarProvider()


@pytest.fixture
def sync_provider():
    return StubSyncProvider()


@pytest.fixture
def sample_envelope():
    """Create a minimal StageEnvelopeV1 for testing."""
    provider_desc = ProviderDescriptorV1(
        provider="test_provider",
        model="test_model",
        version="1.0.0",
        capability="test_capability",
    )
    return StageEnvelopeV1(
        stage_id="S20",
        attempt=1,
        input_hash="a" * 64,
        provider=provider_desc,
    )


@pytest.fixture
def mock_artifact_ref():
    """Create a mock ArtifactRefV1 for testing."""
    return ArtifactRefV1(
        artifact_id="test_artifact_001",
        path="s3://avatar-harness-poc/artifacts/test_artifact.wav",
        hash="b" * 64,
        mime_type="audio/wav",
    )


@pytest.fixture
def mock_video_ref():
    """Create a mock video ArtifactRefV1 for testing."""
    return ArtifactRefV1(
        artifact_id="test_video_001",
        path="s3://avatar-harness-poc/artifacts/test_video.mp4",
        hash="c" * 64,
        mime_type="video/mp4",
    )


def test_voice_provider_interface(voice_provider):
    """Test that voice provider implements the required interface."""
    assert voice_provider.capability == "voice_synthesis"
    assert hasattr(voice_provider, "run")


def test_voice_provider_run(voice_provider, sample_envelope, mock_artifact_ref):
    """Test that voice provider returns a valid StageOutputV1 with VoiceTrackV1."""
    # Need to ensure fixture file exists
    fixture_path = Path(__file__).parent.parent / "fixtures" / "stubs" / "silent_5s.wav"
    if not fixture_path.exists():
        pytest.skip(f"Fixture file not found: {fixture_path}")

    # Mock put_artifact in the stub_voice module directly
    with patch("providers.stub.stub_voice.put_artifact") as mock_put:
        mock_put.return_value = mock_artifact_ref
        
        # Register the provider
        register(voice_provider)

        # Run the provider
        output = voice_provider.run(sample_envelope,"test_run_s20")

        # Check output structure
        assert isinstance(output, StageOutputV1)
        assert "run_id" in output.payload
        assert "voice_id" in output.payload
        assert "audio_artifact" in output.payload
        assert "duration_seconds" in output.payload

        # Check VoiceTrackV1 can be reconstructed
        voice_track = VoiceTrackV1(**output.payload)
        assert voice_track.duration_seconds == 5.0
        assert voice_track.voice_id == "stub_voice_001"
        assert voice_track.audio_artifact.mime_type == "audio/wav"

        # Check artifact refs
        assert len(output.artifact_refs) == 1
        assert output.artifact_refs[0].mime_type == "audio/wav"

        # Check metadata
        assert output.metadata["provider"] == "stub_voice"
        assert output.metadata["stub"] is True
        
        # Verify put_artifact was called
        mock_put.assert_called_once()


def test_avatar_provider_interface(avatar_provider):
    """Test that avatar provider implements the required interface."""
    assert avatar_provider.capability == "avatar_render"
    assert hasattr(avatar_provider, "run")


def test_avatar_provider_run(avatar_provider, sample_envelope, mock_video_ref):
    """Test that avatar provider returns a valid StageOutputV1 with PrimaryVisualTrackV1."""
    fixture_path = Path(__file__).parent.parent / "fixtures" / "stubs" / "black_5s.mp4"
    if not fixture_path.exists():
        pytest.skip(f"Fixture file not found: {fixture_path}")

    # Mock put_artifact in the stub_avatar module directly
    with patch("providers.stub.stub_avatar.put_artifact") as mock_put:
        mock_put.return_value = mock_video_ref
        
        register(avatar_provider)

        # Modify envelope for avatar stage
        sample_envelope.stage_id = "S30"

        output = avatar_provider.run(sample_envelope, "test_run_s30")


        assert isinstance(output, StageOutputV1)
        assert "run_id" in output.payload
        assert "video_artifact" in output.payload

        # Check PrimaryVisualTrackV1 can be reconstructed
        visual_track = PrimaryVisualTrackV1(**output.payload)
        assert visual_track.video_artifact.mime_type == "video/mp4"

        assert len(output.artifact_refs) == 1
        assert output.artifact_refs[0].mime_type == "video/mp4"
        assert output.metadata["provider"] == "stub_avatar"
        assert output.metadata["stub"] is True
        
        mock_put.assert_called_once()


def test_sync_provider_interface(sync_provider):
    """Test that sync provider implements the required interface."""
    assert sync_provider.capability == "media_sync"
    assert hasattr(sync_provider, "run")


def test_sync_provider_run_with_existing_artifact(sync_provider, sample_envelope, mock_video_ref):
    """Test sync provider when video artifact is in envelope."""
    with patch("providers.stub.stub_sync.get_artifact") as mock_get, \
         patch("providers.stub.stub_sync.put_artifact") as mock_put:
        mock_get.return_value = b"fake video bytes"
        stored_ref = ArtifactRefV1(
            artifact_id="sync_test_run_s40",
            path="s3://avatar-harness-poc/artifacts/sync_test_run_s40",
            hash="e" * 64,
            mime_type="video/mp4",
        )
        mock_put.return_value = stored_ref

        register(sync_provider)
        sample_envelope.artifact_refs = [mock_video_ref]
        sample_envelope.stage_id = "S40"

        output = sync_provider.run(sample_envelope, "test_run_s40")

        assert isinstance(output, StageOutputV1)
        assert "run_id" in output.payload
        assert "media_artifact" in output.payload

        sync_media = SynchronizedMediaV1(**output.payload)
        assert sync_media.media_artifact.artifact_id == "sync_test_run_s40"
        assert sync_media.media_artifact.mime_type == "video/mp4"

        assert len(output.artifact_refs) == 1
        assert output.artifact_refs[0].artifact_id == "sync_test_run_s40"
        assert output.metadata["provider"] == "stub_sync"
        assert output.metadata["stub"] is True

        mock_get.assert_called_once_with(mock_video_ref)
        mock_put.assert_called_once()



def test_provider_registration():
    """Test that providers can be registered and retrieved."""
    voice_provider = StubVoiceProvider()
    register(voice_provider)

    retrieved = get("voice_synthesis")
    assert retrieved is voice_provider

    with pytest.raises(KeyError):
        get("nonexistent")


def test_voice_provider_artifact_persistence(voice_provider, sample_envelope, mock_artifact_ref):
    """Test that voice provider actually calls put_artifact with correct parameters."""
    fixture_path = Path(__file__).parent.parent / "fixtures" / "stubs" / "silent_5s.wav"
    if not fixture_path.exists():
        pytest.skip(f"Fixture file not found: {fixture_path}")

    register(voice_provider)

    # Mock put_artifact in the stub_voice module directly (not orchestrator.storage)
    with patch("providers.stub.stub_voice.put_artifact") as mock_put:
        mock_put.return_value = mock_artifact_ref

        output = voice_provider.run(sample_envelope,"test_run_s20")

        # Verify put_artifact was called with correct args
        assert mock_put.called
        call_args = mock_put.call_args
        assert call_args[1]["mime_type"] == "audio/wav"
        assert call_args[1]["artifact_id"].startswith("voice_")
        
        # Verify output has the mock artifact
        assert output.artifact_refs[0].artifact_id == "test_artifact_001"


def test_avatar_provider_artifact_persistence(avatar_provider, sample_envelope, mock_video_ref):
    """Test that avatar provider actually calls put_artifact with correct parameters."""
    fixture_path = Path(__file__).parent.parent / "fixtures" / "stubs" / "black_5s.mp4"
    if not fixture_path.exists():
        pytest.skip(f"Fixture file not found: {fixture_path}")

    register(avatar_provider)
    sample_envelope.stage_id = "S30"

    # Mock put_artifact in the stub_avatar module directly
    with patch("providers.stub.stub_avatar.put_artifact") as mock_put:
        mock_put.return_value = mock_video_ref

        output = avatar_provider.run(sample_envelope, "test_run_s30")

        # Verify put_artifact was called with correct args
        assert mock_put.called
        call_args = mock_put.call_args
        assert call_args[1]["mime_type"] == "video/mp4"
        assert call_args[1]["artifact_id"].startswith("avatar_")
        
        # Verify output has the mock artifact
        assert output.artifact_refs[0].artifact_id == "test_video_001"


def test_sync_provider_copies_artifact(sync_provider, sample_envelope, mock_video_ref):
    """Test that sync provider picks the first video artifact and stores it under its own S40 id."""
    with patch("providers.stub.stub_sync.get_artifact") as mock_get, \
     patch("providers.stub.stub_sync.put_artifact") as mock_put:
        mock_get.return_value = b"fake video bytes"
        stored_ref = ArtifactRefV1(
            artifact_id="sync_test_run_s40",
            path="s3://avatar-harness-poc/artifacts/sync_test_run_s40",
            hash="e" * 64,
            mime_type="video/mp4",
        )
        mock_put.return_value = stored_ref

        register(sync_provider)

        mock_video_ref2 = ArtifactRefV1(
            artifact_id="test_video_002",
            path="s3://bucket/artifacts/test_video2.mp4",
            hash="d" * 64,
            mime_type="video/mp4",
        )
        sample_envelope.artifact_refs = [mock_video_ref, mock_video_ref2]
        sample_envelope.stage_id = "S40"

        output = sync_provider.run(sample_envelope, "test_run_s40")

        # Should read from the first video artifact...
        mock_get.assert_called_once_with(mock_video_ref)
        # ...but the output is always S40's own distinct artifact
        sync_media = SynchronizedMediaV1(**output.payload)
        assert sync_media.media_artifact.artifact_id == "sync_test_run_s40"

def test_stub_script_provider_satisfies_protocol():
    """Verify that StubScriptProvider satisfies the StageProvider Protocol."""
    provider = StubScriptProvider()
    assert isinstance(provider, StageProvider)
    assert provider.capability == "script_generation"


def test_stub_script_provider_run():
    """Verify that StubScriptProvider consumes StageEnvelopeV1 and returns valid StageOutputV1 with ScriptPackageV1 payload."""
    provider = StubScriptProvider()

    envelope = StageEnvelopeV1(
        stage_id="S10",
        attempt=1,
        input_hash="a" * 64,
        artifact_refs=[],
        validation_ref=None,
        provider=ProviderDescriptorV1(
            provider="stub_script_provider",
            model="stub-v1",
            version="1.0.0",
            capability="script_generation",
        ),
    )

    run_id = "run_test_s10"

    mock_artifact_ref = ArtifactRefV1(
        artifact_id="test_script_001",
        path="s3://avatar-harness-poc/artifacts/test_script.json",
        hash="d" * 64,
        mime_type="application/json",
    )

    with patch("providers.stub.stub_script.put_artifact") as mock_put:
        mock_put.return_value = mock_artifact_ref
        output = provider.run(envelope, run_id)

        mock_put.assert_called_once()
        

    assert isinstance(output, StageOutputV1)
    assert output.metadata.get("stub") is True

    # Validate output payload against real M0 ScriptPackageV1 contract
    script_pkg = ScriptPackageV1.model_validate(output.payload)
    assert script_pkg.run_id == run_id
    assert len(script_pkg.scenes) == 3
    assert script_pkg.scenes[0] == "Welcome to this AI avatar demonstration."


def test_stub_qc_provider_satisfies_protocol_and_runs():
    """Verify that StubQCProvider satisfies StageProvider and returns valid QualityReportV1."""
    provider = StubQCProvider()
    assert isinstance(provider, StageProvider)
    assert provider.capability == "quality_control"

    envelope = StageEnvelopeV1(
        stage_id="S70",
        attempt=1,
        input_hash="b" * 64,
        artifact_refs=[
            ArtifactRefV1(
                artifact_id="master_video_test",
                path="s3://avatar-harness-poc/artifacts/master_video_test.mp4",
                hash="d" * 64,
                mime_type="video/mp4",
            )
        ],
        validation_ref=None,
        provider=ProviderDescriptorV1(
            provider="stub_qc_provider",
            model="stub-v1",
            version="1.0.0",
            capability="quality_control",
        ),
    )

    output = provider.run(envelope, "test_run_s70")
    assert isinstance(output, StageOutputV1)
    assert output.metadata.get("passed") is True

    report = QualityReportV1.model_validate(output.payload)
    assert report.run_id == "test_run_s70"
    assert report.master_video_hash == "d" * 64
    assert report.passed is True
    assert "identity_similarity" in report.metrics


def test_stub_disclosure_provider_satisfies_protocol_and_runs():
    """Verify that StubDisclosureProvider satisfies StageProvider and returns valid DisclosureDecisionV1."""
    provider = StubDisclosureProvider()
    assert isinstance(provider, StageProvider)
    assert provider.capability == "disclosure_check"

    envelope = StageEnvelopeV1(
        stage_id="G90",
        attempt=1,
        input_hash="c" * 64,
        artifact_refs=[
            ArtifactRefV1(
                artifact_id="master_video_test",
                path="s3://avatar-harness-poc/artifacts/master_video_test.mp4",
                hash="e" * 64,
                mime_type="video/mp4",
            )
        ],
        validation_ref=None,
        provider=ProviderDescriptorV1(
            provider="stub_disclosure_provider",
            model="stub-v1",
            version="1.0.0",
            capability="disclosure_check",
        ),
    )

    output = provider.run(envelope, "test_run_g90")
    assert isinstance(output, StageOutputV1)
    assert output.metadata.get("contains_synthetic_media") is True

    disclosure = DisclosureDecisionV1.model_validate(output.payload)
    assert disclosure.master_video_hash == "e" * 64
    assert disclosure.contains_synthetic_media is True

def test_sync_provider_raises_without_video_artifact(sync_provider, sample_envelope):
    """S40 with no upstream video artifact should fail loudly, not fall back to a fixture."""
    register(sync_provider)

    sample_envelope.artifact_refs = []
    sample_envelope.stage_id = "S40"

    with pytest.raises(FileNotFoundError, match="requires a video artifact from S30"):
        sync_provider.run(sample_envelope, "test_run_s40")