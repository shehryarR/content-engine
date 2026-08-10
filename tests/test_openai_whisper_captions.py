"""
tests/test_openai_whisper_captions.py
Unit test for OpenAIWhisperCaptionsProvider.
Mocks the OpenAI API call — no real API key needed.
"""
import json
import pytest
from unittest.mock import MagicMock, patch
from contracts.common.envelope import (
    ArtifactRefV1, StageEnvelopeV1, ProviderDescriptorV1
)
from providers.openai_whisper_captions import OpenAIWhisperCaptionsProvider

FAKE_PROVIDER = ProviderDescriptorV1(
    provider="stub",
    model="stub_v1",
    version="1.0.0",
    capability="caption_generation",
)

FAKE_AUDIO_REF = ArtifactRefV1(
    artifact_id="audio_run_001",
    path="s3://bucket/artifacts/" + "a" * 64,
    hash="a" * 64,
    mime_type="audio/wav",
)

FAKE_ENVELOPE = StageEnvelopeV1(
    stage_id="S50",
    attempt=1,
    input_hash="c" * 64,
    artifact_refs=[FAKE_AUDIO_REF],
    provider=FAKE_PROVIDER,
)


@patch("providers.openai_whisper_captions.load_provider_config")
@patch("providers.openai_whisper_captions.get_artifact")
@patch("providers.openai_whisper_captions.put_artifact")
@patch("providers.openai_whisper_captions.OpenAI")
def test_caption_provider_returns_caption_track(
    mock_openai_cls, mock_put, mock_get, mock_cfg
):
    mock_cfg.return_value = {"api_key": "test-key"}
    mock_get.return_value = b"fake audio bytes"

    fake_word = MagicMock()
    fake_word.word = "hello"
    fake_word.start = 0.0
    fake_word.end = 0.5

    fake_transcript = MagicMock()
    fake_transcript.words = [fake_word]

    mock_client = MagicMock()
    mock_client.audio.transcriptions.create.return_value = fake_transcript
    mock_openai_cls.return_value = mock_client

    fake_artifact = ArtifactRefV1(
        artifact_id="captions_run_001",
        path="s3://bucket/artifacts/" + "b" * 64,
        hash="b" * 64,
        mime_type="application/json",
    )
    mock_put.return_value = fake_artifact

    provider = OpenAIWhisperCaptionsProvider()
    output = provider.run(FAKE_ENVELOPE, "run_001")

    assert output.metadata["provider"] == "openai_whisper"
    assert output.metadata["word_count"] == 1
    assert len(output.artifact_refs) == 1

    stored_bytes = mock_put.call_args[1]["data"]
    data = json.loads(stored_bytes)
    assert data["words"][0]["text"] == "hello"
    assert data["words"][0]["start"] == 0.0


@patch("providers.openai_whisper_captions.load_provider_config")
@patch("providers.openai_whisper_captions.OpenAI")
def test_caption_provider_raises_if_no_audio(mock_openai_cls, mock_cfg):
    mock_cfg.return_value = {"api_key": "test-key"}
    mock_openai_cls.return_value = MagicMock()

    empty_envelope = StageEnvelopeV1(
        stage_id="S50",
        attempt=1,
        input_hash="d" * 64,
        artifact_refs=[],
        provider=FAKE_PROVIDER,
    )

    provider = OpenAIWhisperCaptionsProvider()
    with pytest.raises(ValueError, match="S50 requires audio artifact"):
        provider.run(empty_envelope, "run_002")
