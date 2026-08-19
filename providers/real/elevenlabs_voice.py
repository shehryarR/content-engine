import json
import os
import subprocess
import tempfile

from elevenlabs.client import ElevenLabs

from contracts.common.envelope import StageEnvelopeV1, StageOutputV1
from contracts.stages.s20_voice import VoiceTrackV1
from orchestrator.provider_config import load_provider_config
from orchestrator.storage import get_artifact, put_artifact
from orchestrator.telemetry import get_connection

MAX_CHARS_MULTILINGUAL_V2 = 1500  # 10k hard cap, leave headroom
MAX_AUDIO_DURATION_SEC = 50  # ~50s * 176.4KB/s ≈ 8.8MB WAV, safe margin under D-ID's 10MB cap
MIN_SAMPLE_RATE_HZ = 16000
SILENCE_MEAN_VOLUME_DB_THRESHOLD = -50.0  # ffmpeg volumedetect mean_volume at/below this = effectively silent


def _fetch_one(query: str, params: tuple) -> tuple | None:
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(query, params)
            return cur.fetchone()


class ElevenLabsVoiceProvider:
    capability: str = "voice_synthesis"

    def __init__(self):
        config = load_provider_config("voice_synthesis")
        self._client = ElevenLabs(api_key=config["api_key"])
        self._model_id = config.get("model_id", "eleven_multilingual_v2")

    def run(self, envelope: StageEnvelopeV1, run_id: str) -> StageOutputV1:
        script_ref = next(
            (r for r in envelope.artifact_refs if "script" in r.artifact_id),
            None,
        )
        idea_ref = next(
            (r for r in envelope.artifact_refs if "idea" in r.artifact_id),
            None,
        )
        if script_ref is None:
            raise ValueError("S20 requires script artifact from S10")
        if idea_ref is None:
            raise ValueError("S20 requires idea artifact from S00")

        script_data = json.loads(get_artifact(script_ref))
        idea_data = json.loads(get_artifact(idea_ref))

        scenes = script_data.get("scenes", [])
        if not scenes:
            raise ValueError(f"S20 script artifact for run {run_id} has no scenes")

        narration = " ".join(scenes)
        if len(narration) > MAX_CHARS_MULTILINGUAL_V2:
            raise ValueError(
                f"narration is {len(narration)} chars, exceeds {MAX_CHARS_MULTILINGUAL_V2} "
                f"safety cap for {self._model_id}"
            )

        registry_voice_id = idea_data["voice_id"]  # e.g. "voice_001", the consented registry ID

        row = _fetch_one(
            "SELECT provider_voice_id, consent_status FROM voice_profiles WHERE voice_id=%s",
            (registry_voice_id,),
        )
        if row is None:
            raise ValueError(f"voice_id {registry_voice_id} not found in registry")
        provider_voice_id, consent_status = row
        if consent_status != "active":
            raise ValueError(f"voice_id {registry_voice_id} consent status is {consent_status}, not active")
        if not provider_voice_id:
            raise ValueError(f"voice_id {registry_voice_id} has no provider_voice_id configured")

        audio_generator = self._client.text_to_speech.convert(
            text=narration,
            voice_id=provider_voice_id,
            model_id=self._model_id,
            output_format="mp3_44100_128",
        )
        audio_bytes = b"".join(audio_generator)
        estimated_duration_sec = len(audio_bytes) / (128_000 / 8)  # from our known 128kbps MP3 bitrate
        MAX_AUDIO_DURATION_SEC = 50  # ~50s * 176.4KB/s ≈ 8.8MB WAV, safe margin under D-ID's 10MB cap

        if estimated_duration_sec > MAX_AUDIO_DURATION_SEC:
            raise ValueError(
        f"Generated narration is ~{estimated_duration_sec:.0f}s, exceeds the "
        f"{MAX_AUDIO_DURATION_SEC}s cap needed to stay under D-ID's 10MB post-conversion "
        f"WAV limit. Narration was {len(narration)} chars — shorten the script."
    )

        artifact = put_artifact(
            data=audio_bytes,
            artifact_id=f"voice_{run_id}",
            mime_type="audio/mpeg",
        )

        duration_seconds = max(len(audio_bytes) / (128_000 / 8), 0.1)  # rough MP3 estimate, refined later

        voice_track = VoiceTrackV1(
            run_id=run_id,
            voice_id=registry_voice_id,
            audio_artifact=artifact,
            duration_seconds=duration_seconds,
        )

        return StageOutputV1(
            payload=voice_track.model_dump(),
            metadata={
                "provider": "elevenlabs",
                "model": self._model_id,
                "char_count": len(narration),
                "provider_voice_id": provider_voice_id,
            },
            artifact_refs=[artifact],
        )



def _probe_audio_streams(audio_bytes: bytes, suffix: str) -> list[dict]:
    """ffprobe every stream in the audio bytes. Returns [] if the file
    can't be probed at all (corrupt/unreadable) rather than raising -
    the empty list itself is the "corrupt" signal to the caller."""
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as f:
        f.write(audio_bytes)
        tmp_path = f.name
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_streams", tmp_path],
            capture_output=True, text=True, timeout=10,
        )
        data = json.loads(result.stdout)
        return data.get("streams", [])
    except Exception:
        return []
    finally:
        os.unlink(tmp_path)


def _measure_mean_volume_db(audio_bytes: bytes, suffix: str) -> float | None:
    """Uses ffmpeg's volumedetect filter to get mean_volume in dB.
    Returns None if it can't be determined (e.g. corrupt input) - the
    caller treats None as "can't assess silence", not as silent."""
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as f:
        f.write(audio_bytes)
        tmp_path = f.name
    try:
        result = subprocess.run(
            ["ffmpeg", "-i", tmp_path, "-af", "volumedetect", "-f", "null", "-"],
            capture_output=True, text=True, timeout=15,
        )
        for line in result.stderr.splitlines():
            if "mean_volume:" in line:
                return float(line.split("mean_volume:")[1].strip().split(" ")[0])
        return None
    except Exception:
        return None
    finally:
        os.unlink(tmp_path)


def validate_voice(
    audio_bytes: bytes,
    mime_type: str = "audio/mpeg",
    max_duration_seconds: float = MAX_AUDIO_DURATION_SEC,
) -> tuple[bool, list[str]]:
    """
    Checks: audio is genuinely probeable (not corrupt/truncated), has an
    audio stream with a sane sample rate, isn't effectively silent, and
    is within the D-ID-driven duration cap. Returns (passed, failures).

    Codec is deliberately not hard-checked here - ElevenLabs' own
    output_format setting already pins the codec at generation time
    (mp3_44100_128 in run() above); this validator is about catching a
    corrupt/desynced/oversized result, not re-litigating provider config.
    """
    failures: list[str] = []
    suffix = ".wav" if "wav" in mime_type else ".mp3"

    streams = _probe_audio_streams(audio_bytes, suffix=suffix)
    if not streams:
        return False, ["audio could not be probed - file is corrupt or unreadable"]

    audio_stream = next((s for s in streams if s.get("codec_type") == "audio"), None)
    if audio_stream is None:
        return False, ["no audio stream present in the file"]

    sample_rate = int(audio_stream.get("sample_rate", 0) or 0)
    if sample_rate and sample_rate < MIN_SAMPLE_RATE_HZ:
        failures.append(
            f"sample rate {sample_rate}Hz is below the {MIN_SAMPLE_RATE_HZ}Hz minimum"
        )

    duration = float(audio_stream.get("duration", 0.0) or 0.0)
    if duration <= 0:
        failures.append("audio has zero/invalid duration")
    elif duration > max_duration_seconds:
        failures.append(
            f"audio duration {duration:.2f}s exceeds the {max_duration_seconds}s cap"
        )

    mean_volume = _measure_mean_volume_db(audio_bytes, suffix=suffix)
    if mean_volume is not None and mean_volume <= SILENCE_MEAN_VOLUME_DB_THRESHOLD:
        failures.append(
            f"audio is effectively silent (mean volume {mean_volume:.1f}dB, "
            f"threshold {SILENCE_MEAN_VOLUME_DB_THRESHOLD}dB)"
        )

    return len(failures) == 0, failures


def compute_speaker_similarity(
    reference_bytes: bytes,
    generated_bytes: bytes,
    ref_suffix: str = ".wav",
    gen_suffix: str = ".wav",
) -> float:
    """Compute a speaker-consistency score in [0.0, 1.0] between a reference
    voice sample and a freshly generated narration.

    Implementation: extracts raw mono 16kHz PCM from both clips via ffmpeg,
    then computes cosine similarity on their mean power spectra (using numpy
    FFT). This is a lightweight proxy for speaker identity that:
      - requires no model download (CI-safe, always offline)
      - is deterministic for the same input bytes
      - correlates well enough with voice matching for threshold gating

    The 0.72 threshold in voice_threshold_v1.json was calibrated against
    accepted_v1 (same-speaker, score ≥ 0.80) and negative_v1 (different-
    speaker, score ≤ 0.55) fixture pairs, with 0.72 as the midpoint guard.

    Returns 0.0 if either clip cannot be decoded (treated as mismatch, not
    as an error, so the validator reports speaker_similarity_low rather than
    crashing with an internal exception).
    """
    import numpy as np

    def _extract_pcm_mono_16k(audio_bytes: bytes, suffix: str) -> np.ndarray | None:
        """Decode to raw 16kHz mono f32le PCM via ffmpeg pipe."""
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as f:
            f.write(audio_bytes)
            tmp_path = f.name
        try:
            result = subprocess.run(
                [
                    "ffmpeg", "-y", "-i", tmp_path,
                    "-ac", "1", "-ar", "16000",
                    "-f", "f32le", "pipe:1",
                ],
                capture_output=True,
                timeout=20,
            )
            if not result.stdout:
                return None
            samples = np.frombuffer(result.stdout, dtype=np.float32)
            return samples if len(samples) > 0 else None
        except Exception:
            return None
        finally:
            os.unlink(tmp_path)

    def _mean_power_spectrum(samples: "np.ndarray", n_fft: int = 2048) -> "np.ndarray":
        """Mean power spectrum across overlapping frames."""
        import numpy as np
        hop = n_fft // 2
        frames = [
            samples[i : i + n_fft]
            for i in range(0, len(samples) - n_fft, hop)
        ]
        if not frames:
            return np.abs(np.fft.rfft(samples, n=n_fft)) ** 2
        spectra = np.array([np.abs(np.fft.rfft(f, n=n_fft)) ** 2 for f in frames])
        return spectra.mean(axis=0)

    import numpy as np

    ref_samples = _extract_pcm_mono_16k(reference_bytes, ref_suffix)
    gen_samples = _extract_pcm_mono_16k(generated_bytes, gen_suffix)

    if ref_samples is None or gen_samples is None:
        return 0.0

    ref_spec = _mean_power_spectrum(ref_samples)
    gen_spec = _mean_power_spectrum(gen_samples)

    # Cosine similarity on power spectra — same length guaranteed by rfft(n=2048).
    dot = float(np.dot(ref_spec, gen_spec))
    norm = float(np.linalg.norm(ref_spec) * np.linalg.norm(gen_spec))
    if norm == 0.0:
        return 0.0
    # Clamp to [0, 1] — power spectra are non-negative so dot product
    # is always ≥ 0, but numerical noise can push slightly above 1.0.
    return min(1.0, max(0.0, dot / norm))