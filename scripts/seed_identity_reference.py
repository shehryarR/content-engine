import argparse
import sys
import requests
from PIL import Image
import uuid
import io
import re
from pathlib import Path
#     uv run python scripts/seed_identity_reference.py identity_002 "Presenter Name" <url>
#     uv run python scripts/seed_identity_reference.py identity_002 "Presenter Name" <url> --max-dim 2048
#     uv run python scripts/seed_identity_reference.py identity_002 "Presenter Name" <path_to_local.png> --raw


from orchestrator.storage import put_artifact
from orchestrator.manifest_store import get_connection


def resolve_url(url: str) -> str:
    file_id = url
    if "drive.google.com" in url:
        match = re.search(r"/d/([a-zA-Z0-9_-]+)", url) or re.search(r"id=([a-zA-Z0-9_-]+)", url)
        file_id = match.group(1)
    return f"https://drive.google.com/uc?export=download&id={file_id}"


DEFAULT_MAX_DIMENSION_PX = 1024  # Downscaling matters for staying under D-ID's
                                  # 10MB PNG cap on large photos, not for face
                                  # detection quality — LANCZOS at this size
                                  # preserves far more resolution than any
                                  # face detector needs. Override with
                                  # --max-dim if you want to test that theory
                                  # yourself, or use --raw to skip resizing
                                  # (and re-encoding) entirely.


def validate_and_normalize_image(data: bytes, max_dimension_px: int) -> bytes:
    """Validate the source image and re-encode it as real, size-safe PNG bytes.

    identity_profiles has no mime_type column, and downstream code
    (activities.fetch_identity_reference, providers/real/did_avatar.py)
    hardcodes mime_type="image/png" for every identity reference. If the
    source photo is actually a JPEG, that mismatch sends real JPEG bytes
    to D-ID labeled as PNG, which D-ID can fail to decode correctly during
    /talks (surfacing as an unrelated-looking "file size exceeded" error).
    Re-encoding here makes the hardcoded label true instead of trying to
    thread the real mime type through the DB and every downstream reader.

    Lossless PNG re-encoding of a large photographic JPEG can end up
    *larger* than the original — downscaling first keeps it comfortably
    under D-ID's 10MB cap regardless of the source photo's resolution.
    """
    if len(data) > 10 * 1024 * 1024:
        raise ValueError("Image exceeds 10MB — D-ID's limit")
    img = Image.open(io.BytesIO(data))
    if img.format not in ("JPEG", "PNG"):
        raise ValueError(f"Unsupported format {img.format} — must be JPEG or PNG")

    if img.mode != "RGB":
        img = img.convert("RGB")

    if max_dimension_px and max(img.size) > max_dimension_px:
        img.thumbnail((max_dimension_px, max_dimension_px), Image.LANCZOS)

    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    png_bytes = buf.getvalue()

    if len(png_bytes) > 10 * 1024 * 1024:
        raise ValueError(
            f"Re-encoded PNG is {len(png_bytes) / 1_000_000:.2f} MB even "
            f"after resizing to {max_dimension_px}px — source image is "
            "unusually complex, try a smaller --max-dim"
        )
    return png_bytes


def load_raw_png_bytes(path_or_url: str) -> bytes:
    """--raw mode: skip all resizing/re-encoding. The caller is responsible
    for supplying bytes that are already genuine PNG and under 10MB — this
    only does the minimum sanity check so a bad file fails fast with a
    clear message instead of a confusing D-ID error three stages later."""
    p = Path(path_or_url)
    if p.exists():
        data = p.read_bytes()
    else:
        resp = requests.get(resolve_url(path_or_url))
        resp.raise_for_status()
        data = resp.content

    if len(data) > 10 * 1024 * 1024:
        raise ValueError(
            f"--raw file is {len(data) / 1_000_000:.2f} MB — exceeds D-ID's "
            "10MB limit. --raw does not resize for you."
        )
    img = Image.open(io.BytesIO(data))
    if img.format != "PNG":
        raise ValueError(
            f"--raw requires a genuine PNG file (got {img.format}). "
            "Downstream code hardcodes mime_type=\"image/png\" regardless "
            "of what you upload, so a mislabeled JPEG will break D-ID's "
            "decode step — convert it yourself first if you're going --raw."
        )
    return data


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("identity_id")
    parser.add_argument("display_name")
    parser.add_argument("source", help="URL (incl. Google Drive) or local file path")
    parser.add_argument(
        "--max-dim", type=int, default=DEFAULT_MAX_DIMENSION_PX,
        help=f"Max width/height in px before re-encoding (default {DEFAULT_MAX_DIMENSION_PX}). "
             "Set to 0 to disable resizing (re-encode at full resolution).",
    )
    parser.add_argument(
        "--raw", action="store_true",
        help="Skip resizing AND re-encoding entirely. Requires the source "
             "to already be a genuine PNG under 10MB (local path or URL). "
             "Use this if you want full control over how the image was "
             "prepared and just want it uploaded as-is.",
    )
    args = parser.parse_args()

    if args.raw:
        image_bytes = load_raw_png_bytes(args.source)
    else:
        url = resolve_url(args.source)
        resp = requests.get(url)
        resp.raise_for_status()
        image_bytes = validate_and_normalize_image(resp.content, args.max_dim)

    artifact = put_artifact(
        data=image_bytes,
        artifact_id=f"identity_ref_{args.identity_id}",
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
                (args.identity_id, args.display_name, artifact.path, artifact.hash, consent_grant_id),
            )
            conn.commit()

    print(f"Created identity {args.identity_id} — {artifact.path} (hash {artifact.hash[:12]}...)")


if __name__ == "__main__":
    main()