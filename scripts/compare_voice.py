"""
scripts/compare_voice.py

Standalone repro/inspection tool for the S20 speaker-similarity check -
mirrors scripts/compare_identity.py's shape for the S30 identity check,
but for voice. Compares a reference voice sample against a generated
narration clip using the same compute_speaker_similarity() the real S20
validator calls.

No MinIO/Postgres/Temporal needed - this only touches local file bytes
and calls ffmpeg directly, same as the underlying function does.

Unlike compare_identity.py there's no frame to extract and look at -
audio has to be listened to, not viewed - so this just prints both file
paths so you can play them back yourself alongside the score.

Usage:
    uv run python scripts/compare_voice.py
    uv run python scripts/compare_voice.py --reference path/to/sample.mp3 --generated path/to/narration.wav
"""

import argparse
import json
import sys
from pathlib import Path

from providers.real.elevenlabs_voice import compute_speaker_similarity

DEFAULT_REFERENCE = Path("fixtures/stubs/voice_5s.mp3")
DEFAULT_GENERATED = Path(
    "fixtures/stubs/voice_run_20260810_151321_851f694f20e75967989ad4eb580b443f15f0020dbafa12d370f4135bed639365.mp3"
)
THRESHOLD_PATH = Path("configs/policy/voice_threshold_v1.json")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reference", type=Path, default=DEFAULT_REFERENCE,
                         help=f"Reference voice sample (default: {DEFAULT_REFERENCE})")
    parser.add_argument("--generated", type=Path, default=DEFAULT_GENERATED,
                         help=f"Generated narration clip (default: {DEFAULT_GENERATED})")
    args = parser.parse_args()

    if not args.reference.exists():
        print(f"Error: reference audio not found: {args.reference}")
        sys.exit(1)
    if not args.generated.exists():
        print(f"Error: generated audio not found: {args.generated}")
        sys.exit(1)

    reference_bytes = args.reference.read_bytes()
    generated_bytes = args.generated.read_bytes()

    score = compute_speaker_similarity(
        reference_bytes,
        generated_bytes,
        ref_suffix=args.reference.suffix,
        gen_suffix=args.generated.suffix,
    )

    try:
        threshold = json.loads(THRESHOLD_PATH.read_text())["min_score"]
    except Exception:
        threshold = None

    print(f"reference sample : {args.reference}  (play this)")
    print(f"generated clip   : {args.generated}  (and this - listen for yourself)")
    print()
    print(f"speaker_similarity score : {score:.4f}")
    if threshold is not None:
        print(f"threshold (min_score)    : {threshold}")
        print(f"would pass                : {score >= threshold}")
    print()
    print("Per voice_threshold_v1.json's calibration_notes: same-speaker pairs")
    print("scored >= 0.80 during calibration, different-speaker pairs <= 0.55.")
    print("A score far outside either range with two clips that sound the same")
    print("to you by ear is worth investigating the same way the S30 identity")
    print("check's framing bug was found - don't assume the number is right")
    print("just because the code runs.")


if __name__ == "__main__":
    main()