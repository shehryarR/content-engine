import json

from elevenlabs.client import ElevenLabs

from contracts.common.envelope import StageEnvelopeV1, StageOutputV1
from contracts.stages.s20_voice import VoiceTrackV1
from orchestrator.provider_config import load_provider_config
from orchestrator.storage import get_artifact, put_artifact
from orchestrator.telemetry import get_connection

MAX_CHARS_MULTILINGUAL_V2 = 9500  # 10k hard cap, leave headroom


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