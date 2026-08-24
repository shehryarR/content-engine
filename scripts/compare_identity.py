"""
scripts/compare_identity.py

Standalone repro for the S30 identity-similarity framing bug: compares a
registry reference photo against a generated avatar video using the same
compute_identity_similarity() the real S30 validator calls, and saves the
extracted video frame to disk so the framing mismatch can be inspected
visually, not just as a number.

No MinIO/Postgres/Temporal needed - this only touches local file bytes
and calls ffmpeg directly, same as the underlying function does.

Usage:
    uv run python scripts/compare_identity.py
    uv run python scripts/compare_identity.py --reference path/to/photo.png --video path/to/clip.mp4
"""

import argparse
import json
import sys
from pathlib import Path

from providers.real.did_avatar import (
    compute_identity_similarity,
    _extract_representative_frame_png,
)

DEFAULT_REFERENCE = Path("fixtures/stubs/mona_lisa.png")
DEFAULT_VIDEO = Path("fixtures/stubs/did_smoke_test.mp4")
THRESHOLD_PATH = Path("configs/policy/identity_threshold_v1.json")
FRAME_OUTPUT_PATH = Path("/tmp/compare_identity_extracted_frame.png")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reference", type=Path, default=DEFAULT_REFERENCE,
                         help=f"Reference identity photo (default: {DEFAULT_REFERENCE})")
    parser.add_argument("--video", type=Path, default=DEFAULT_VIDEO,
                         help=f"Generated avatar video (default: {DEFAULT_VIDEO})")
    args = parser.parse_args()

    if not args.reference.exists():
        print(f"Error: reference image not found: {args.reference}")
        sys.exit(1)
    if not args.video.exists():
        print(f"Error: video not found: {args.video}")
        sys.exit(1)

    reference_bytes = args.reference.read_bytes()
    video_bytes = args.video.read_bytes()

    # Save the extracted frame to disk so it can actually be looked at,
    # not just scored - this is what reveals the framing mismatch.
    frame_bytes = _extract_representative_frame_png(video_bytes)
    if frame_bytes is None:
        print("Error: could not extract a frame from the video (ffmpeg failed).")
        sys.exit(1)
    FRAME_OUTPUT_PATH.write_bytes(frame_bytes)

    score = compute_identity_similarity(reference_bytes, video_bytes)

    try:
        threshold = json.loads(THRESHOLD_PATH.read_text())["min_score"]
    except Exception:
        threshold = None

    print(f"reference photo : {args.reference}")
    print(f"video           : {args.video}")
    print(f"extracted frame : {FRAME_OUTPUT_PATH}  (open this next to the reference photo)")
    print()
    print(f"identity_similarity score : {score:.4f}")
    if threshold is not None:
        print(f"threshold (min_score)     : {threshold}")
        print(f"would pass                : {score >= threshold}")
    print()
    if score < 0.7:
        print("Low score with a visually-same subject usually means a framing")
        print("mismatch (wide/full-body reference vs. tight face-crop video frame),")
        print("not an actual identity mismatch - open the two images and compare.")


if __name__ == "__main__":
    main()