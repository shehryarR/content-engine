"""
providers/openai_whisper_captions.py
S50 Caption Provider — OpenAI Whisper API.
Sends the audio artifact from S20 to OpenAI's whisper-1 endpoint,
gets word-level timestamps back, stores as JSON, returns CaptionTrackV1.
"""
import json
import tempfile
import os
from openai import OpenAI
from contracts.common.envelope import StageEnvelopeV1, StageOutputV1
from contracts.stages.s50_captions import CaptionTrackV1
from orchestrator.provider_config import load_provider_config
from orchestrator.storage import get_artifact, put_artifact


class OpenAIWhisperCaptionsProvider:
    capability: str = 'caption_generation'

    def __init__(self):
        config = load_provider_config('caption_generation')
        self._client = OpenAI(api_key=config['api_key'])

    def run(self, envelope: StageEnvelopeV1, run_id: str) -> StageOutputV1:
        # Find audio artifact from S20
        audio_ref = next(
            (r for r in envelope.artifact_refs
             if r.mime_type and 'audio' in r.mime_type),
            None
        )
        if audio_ref is None:
            raise ValueError('S50 requires audio artifact from S20')

        audio_bytes = get_artifact(audio_ref)

        # Write to temp file — OpenAI client needs a file object
        with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as f:
            f.write(audio_bytes)
            tmp_path = f.name

        try:
            with open(tmp_path, 'rb') as audio_file:
                transcript = self._client.audio.transcriptions.create(
                    model='whisper-1',
                    file=audio_file,
                    response_format='verbose_json',
                    timestamp_granularities=['word'],
                )
        finally:
            os.unlink(tmp_path)

        words = []
        for w in getattr(transcript, 'words', []):
            words.append({
                'text': w.word,
                'start': w.start,
                'end': w.end,
            })

        captions_data = {'words': words}
        caption_bytes = json.dumps(captions_data).encode('utf-8')
        artifact = put_artifact(
            data=caption_bytes,
            artifact_id=f'captions_{run_id}',
            mime_type='application/json',
        )

        caption_track = CaptionTrackV1(
            run_id=run_id,
            captions_artifact=artifact,
            word_count=len(words),
        )
        return StageOutputV1(
            payload=caption_track.model_dump(mode='json'),
            metadata={
                'provider': 'openai_whisper',
                'model': 'whisper-1',
                'word_count': len(words),
            },
            artifact_refs=[artifact],
        )
