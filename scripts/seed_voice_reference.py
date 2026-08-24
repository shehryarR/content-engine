# scripts/seed_voice_reference.py
#
#     uv run python scripts/seed_voice_reference.py voice_001 "Presenter Name" <elevenlabs_voice_id> <path_to_reference_sample.wav>
#
# Mirrors scripts/seed_identity_reference.py's pattern: uploads a real
# reference sample to storage, then records its path + hash in the DB so
# _fetch_reference_voice_bytes() in stage_executor.py can actually find it.
# Previously this script only wrote provider_voice_id and consent metadata -
# voice_profiles had no column for a reference sample at all, so the S20
# speaker-similarity check silently had nothing to compare against.

import sys
from pathlib import Path

from orchestrator.storage import put_artifact
from orchestrator.manifest_store import get_connection

import uuid


def main():
    voice_id, display_name, provider_voice_id, sample_path = (
        sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
    )

    sample_bytes = Path(sample_path).read_bytes()
    suffix = Path(sample_path).suffix.lower()
    mime_type = "audio/mpeg" if suffix == ".mp3" else "audio/wav"

    artifact = put_artifact(
        data=sample_bytes,
        artifact_id=f"voice_ref_{voice_id}",
        mime_type=mime_type,
    )

    consent_grant_id = f"consent_{uuid.uuid4().hex[:8]}"

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO voice_profiles
                    (voice_id, display_name, provider_voice_id, reference_asset, reference_sample_hash, consent_grant_id, consent_status)
                VALUES (%s, %s, %s, %s, %s, %s, 'active')
                ON CONFLICT (voice_id) DO UPDATE SET
                    provider_voice_id = EXCLUDED.provider_voice_id,
                    reference_asset = EXCLUDED.reference_asset,
                    reference_sample_hash = EXCLUDED.reference_sample_hash,
                    consent_status = 'active'
                """,
                (voice_id, display_name, provider_voice_id, artifact.path, artifact.hash, consent_grant_id),
            )
            conn.commit()

    print(f"Seeded voice {voice_id} -> ElevenLabs voice_id {provider_voice_id}")
    print(f"Reference sample: {artifact.path} (hash {artifact.hash[:12]}...)")


if __name__ == "__main__":
    main()