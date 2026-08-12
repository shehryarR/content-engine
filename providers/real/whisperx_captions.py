import whisperx
import json
import tempfile
import os
from contracts.common.envelope import StageEnvelopeV1, StageOutputV1
from contracts.stages.s50_captions import CaptionTrackV1
from orchestrator.provider_config import load_provider_config
from orchestrator.storage import get_artifact, put_artifact

class WhisperXCaptionsProvider:
    capability: str = 'caption_generation'

    def __init__(self):
        config = load_provider_config('caption_generation')
        self._device = config.get('device', 'cpu')
        self._model_size = config.get('model_size', 'base')
        self._model = whisperx.load_model(
            self._model_size, self._device, compute_type='int8'
        )

    def run(self, envelope: StageEnvelopeV1, run_id: str) -> StageOutputV1:
        # Find the audio artifact from S20
        audio_ref = next(
            (r for r in envelope.artifact_refs
             if r.mime_type and 'audio' in r.mime_type),
            None
        )
        if audio_ref is None:
            raise ValueError('S50 requires audio artifact from S20')

        audio_bytes = get_artifact(audio_ref)

        # Write to temp file (whisperx requires file path)
        with tempfile.NamedTemporaryFile(suffix='.mp3', delete=False) as f:
            f.write(audio_bytes)
            tmp_path = f.name

        try:
            result = self._model.transcribe(tmp_path, batch_size=16)
            # Word-level alignment
            model_a, metadata = whisperx.load_align_model(
                language_code=result['language'], device=self._device
            )
            aligned = whisperx.align(
                result['segments'], model_a, metadata, tmp_path, self._device
            )
        finally:
            os.unlink(tmp_path)

        words = []
        for seg in aligned.get('word_segments', []):
            words.append({
                'text': seg['word'],
                'start': seg.get('start', 0.0),
                'end': seg.get('end', 0.0),
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
                'provider': 'whisperx',
                'model_size': self._model_size,
                'word_count': len(words),
            },
            artifact_refs=[artifact],
        )
