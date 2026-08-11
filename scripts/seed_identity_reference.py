import sys
import requests
from PIL import Image
import uuid
import io
import re
#     uv run python scripts/seed_identity_reference.py identity_002 "Presenter Name" <url>


from orchestrator.storage import put_artifact
from orchestrator.manifest_store import get_connection


def resolve_url(url: str) -> str:
    file_id = url
    if "drive.google.com" in url:
        match = re.search(r"/d/([a-zA-Z0-9_-]+)", url) or re.search(r"id=([a-zA-Z0-9_-]+)", url)
        file_id = match.group(1)
    return f"https://drive.google.com/uc?export=download&id={file_id}"


def validate_image(data: bytes):
    if len(data) > 10 * 1024 * 1024:
        raise ValueError("Image exceeds 10MB — D-ID's limit")
    img = Image.open(io.BytesIO(data))
    if img.format not in ("JPEG", "PNG"):
        raise ValueError(f"Unsupported format {img.format} — must be JPEG or PNG")


def main():
    identity_id, display_name, source_url = sys.argv[1], sys.argv[2], sys.argv[3]
    url = resolve_url(source_url)

    resp = requests.get(url)
    resp.raise_for_status()
    image_bytes = resp.content
    validate_image(image_bytes)

    artifact = put_artifact(
        data=image_bytes,
        artifact_id=f"identity_ref_{identity_id}",
        mime_type="image/png",
    )

    consent_grant_id = f"consent_{uuid.uuid4().hex[:8]}"

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO identity_profiles
                    (identity_id, display_name, reference_asset, reference_sample_hash, consent_grant_id, consent_status)
                VALUES (%s, %s, %s, %s, %s, 'active')
                ON CONFLICT (identity_id) DO UPDATE SET
                    reference_asset = EXCLUDED.reference_asset,
                    reference_sample_hash = EXCLUDED.reference_sample_hash,
                    consent_status = 'active'
                """,
                (identity_id, display_name, artifact.path, artifact.hash, consent_grant_id),
            )
            conn.commit()

    print(f"Created identity {identity_id} — {artifact.path} (hash {artifact.hash[:12]}...)")


if __name__ == "__main__":
    main()