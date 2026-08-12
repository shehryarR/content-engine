# scripts/seed_voice_reference.py
import sys
from orchestrator.manifest_store import get_connection

def main():
    voice_id, display_name, provider_voice_id = sys.argv[1], sys.argv[2], sys.argv[3]
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO voice_profiles
                    (voice_id, display_name, provider_voice_id, consent_grant_id, consent_status)
                VALUES (%s, %s, %s, %s, 'active')
                ON CONFLICT (voice_id) DO UPDATE SET
                    provider_voice_id = EXCLUDED.provider_voice_id,
                    consent_status = 'active'
                """,
                (voice_id, display_name, provider_voice_id, f"consent_{voice_id}"),
            )
            conn.commit()
    print(f"Seeded voice {voice_id} -> ElevenLabs voice_id {provider_voice_id}")

if __name__ == "__main__":
    main()