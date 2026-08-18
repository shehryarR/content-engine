"""
providers/real/qc_model_judge.py

S70 subjective quality check - model-judge fallback (M3 Day 3, item 4).

Per M3 Day 1's own rule: a model judge is ONLY for subjective review that
has no deterministic metric - never for codec, timing, identity, speaker,
hash, approval, or publish checks, all of which have real deterministic
or metric validators elsewhere in the pipeline. This provider covers
exactly the remaining gap: whether the assembled video looks like a
finished, presentable output (visible artifacts, obviously wrong framing,
garbled burned-in captions) - not measurable via ffprobe.

Returns a bounded, typed result - never free text - so the caller can
gate on it mechanically rather than parsing prose.
"""

import json
import os
import subprocess
import tempfile

from google import genai

from orchestrator.provider_config import load_provider_config

# Below this confidence, treat the verdict as unreliable rather than
# trusting a low-confidence "passed": routes to human review either way
# per M3 Day 1's human-fallback rule, same as an explicit failed=True.
MIN_CONFIDENCE = 0.6

_JUDGE_SYSTEM_PROMPT = """You are reviewing a short AI-avatar video for \
presentation quality only - NOT for factual accuracy, script content, or \
anything covered by deterministic checks elsewhere in the pipeline. You \
are looking at 3 sampled frames from the video plus its caption text.

Flag ONLY: visible rendering artifacts or glitches, obviously wrong or \
broken framing (e.g. subject cut off, black/blank frame), garbled or \
overlapping burned-in captions.

Do NOT flag: normal video compression softness, minor lighting variation, \
anything about what the speaker is saying.

Respond with ONLY a JSON object, no other text, no markdown fences:
{"passed": true or false, "confidence": a float 0.0-1.0, "rationale": \
"one short sentence explaining the verdict"}"""


def _extract_keyframes(video_bytes: bytes) -> list[bytes]:
    """Sample 3 frames at 25%/50%/75% of the video's duration as JPEG
    bytes, same ffprobe-then-ffmpeg pattern as the existing duration
    helpers (providers/real/assembly.py's _measure_duration)."""
    with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as f:
        f.write(video_bytes)
        video_path = f.name

    try:
        probe = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json",
             "-show_streams", video_path],
            capture_output=True, text=True, timeout=10,
        )
        streams = json.loads(probe.stdout).get("streams", [])
        duration = 0.0
        for s in streams:
            if s.get("codec_type") == "video" and s.get("duration"):
                duration = float(s["duration"])
                break
        if duration <= 0:
            duration = 5.0  # fallback so we still attempt extraction

        frames = []
        for fraction in (0.25, 0.5, 0.75):
            timestamp = duration * fraction
            with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as frame_f:
                frame_path = frame_f.name
            try:
                result = subprocess.run(
                    ["ffmpeg", "-y", "-ss", str(timestamp), "-i", video_path,
                     "-frames:v", "1", "-q:v", "3", frame_path],
                    capture_output=True, timeout=15,
                )
                if result.returncode == 0 and os.path.getsize(frame_path) > 0:
                    with open(frame_path, "rb") as jf:
                        frames.append(jf.read())
            finally:
                if os.path.exists(frame_path):
                    os.unlink(frame_path)
        return frames
    finally:
        os.unlink(video_path)


def judge_video_quality(video_bytes: bytes, caption_text: str) -> dict:
    """
    Runs the subjective quality check. Returns a bounded dict:
    {"passed": bool, "confidence": float, "rationale": str}.

    Raises on infrastructure failure (missing API key, no frames
    extractable, malformed model response) rather than silently
    returning a fake pass - the caller (stage_executor.py's
    _validate_qc_stage) is responsible for deciding how to treat a
    provider error (currently: skip the subjective check, don't fail
    the whole stage over judge infrastructure issues).
    """
    config = load_provider_config("qc_model_judge")
    if not config.get("api_key"):
        raise RuntimeError("qc_model_judge requires an api_key (configs/providers/qc_model_judge.yaml)")

    frames = _extract_keyframes(video_bytes)
    if not frames:
        raise RuntimeError("could not extract any keyframes from video for model judge review")

    client = genai.Client(api_key=config["api_key"])
    model_name = config.get("model_id", "gemini-1.5-flash")

    contents = [_JUDGE_SYSTEM_PROMPT, f"Caption text: {caption_text}"]
    for frame_bytes in frames:
        contents.append({"mime_type": "image/jpeg", "data": frame_bytes})

    response = client.models.generate_content(
        model=model_name,
        contents=contents,
    )

    raw = response.text.strip()
    # Strip accidental markdown fences - models sometimes ignore the
    # "no markdown fences" instruction.
    if raw.startswith("```"):
        raw = raw.strip("`").removeprefix("json").strip()

    parsed = json.loads(raw)

    if not isinstance(parsed.get("passed"), bool):
        raise ValueError(f"model judge response missing/invalid 'passed' field: {raw}")
    confidence = float(parsed.get("confidence", 0.0))
    if not (0.0 <= confidence <= 1.0):
        raise ValueError(f"model judge confidence out of range [0,1]: {confidence}")

    return {
        "passed": parsed["passed"],
        "confidence": confidence,
        "rationale": str(parsed.get("rationale", "")),
    }
