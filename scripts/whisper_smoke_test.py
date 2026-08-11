"""
scripts/whisper_smoke_test.py
Smoke test for OpenAI Whisper captions provider.
Usage: uv run python scripts/whisper_smoke_test.py
"""
from orchestrator.provider_config import load_provider_config
from openai import OpenAI
import json

cfg = load_provider_config('caption_generation')
client = OpenAI(api_key=cfg['api_key'])

# Use the stub audio fixture for smoke test
audio_path = "fixtures/stubs/silent_5s.wav"

print(f"Testing Whisper API with: {audio_path}")
with open(audio_path, 'rb') as f:
    transcript = client.audio.transcriptions.create(
        model='whisper-1',
        file=f,
        response_format='verbose_json',
        timestamp_granularities=['word'],
    )

words = getattr(transcript, 'words', [])
print(f"Words returned: {len(words)}")
for w in words:
    print(f"  {w.word} [{w.start:.2f}s - {w.end:.2f}s]")
print("Smoke test passed!")
