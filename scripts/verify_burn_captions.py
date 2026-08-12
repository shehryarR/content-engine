"""Manual verification for S60 caption burn-in. Not a pytest test — run directly and inspect the output video."""
from pathlib import Path
from providers.stub.stub_assembly import _burn_captions, _words_to_srt

TEST_VIDEO = Path("/tmp/test_input.mp4")
OUTPUT_VIDEO = Path("/tmp/burned_test.mp4")

fake_words = [
    {"text": "This", "start": 0.0, "end": 0.3},
    {"text": "is", "start": 0.3, "end": 0.5},
    {"text": "a", "start": 0.5, "end": 0.6},
    {"text": "caption", "start": 0.6, "end": 1.0},
    {"text": "burn-in", "start": 1.0, "end": 1.5},
    {"text": "test.", "start": 1.5, "end": 1.9},
    {"text": "Second", "start": 2.5, "end": 2.9},
    {"text": "line", "start": 2.9, "end": 3.2},
    {"text": "of", "start": 3.2, "end": 3.3},
    {"text": "captions", "start": 3.3, "end": 3.9},
    {"text": "here.", "start": 3.9, "end": 4.3},
]

print("=== SRT preview ===")
print(_words_to_srt(fake_words))

print("=== Burning captions ===")
video_bytes = TEST_VIDEO.read_bytes()
burned_bytes = _burn_captions(video_bytes, fake_words)
OUTPUT_VIDEO.write_bytes(burned_bytes)
print(f"Done. Wrote {len(burned_bytes)} bytes to {OUTPUT_VIDEO}")
