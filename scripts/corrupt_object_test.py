#!/usr/bin/env python3
"""
scripts/corrupt_object_test.py

M6 (Owner A / Ammar): prove SHA-256 verification catches
corrupted/missing objects in MinIO.

Usage:
    uv run python scripts/corrupt_object_test.py
"""

from __future__ import annotations

import hashlib
import sys

sys.path.insert(0, ".")

from orchestrator.storage import put_artifact, get_artifact, _make_s3_client, BUCKET


def main() -> int:
    print("M6 Corrupt Object Test")
    print("=" * 60)
    passed = 0
    failed = 0

    # 1. Store a known artifact
    print("\n[1] Storing test artifact...")
    test_data = b"M6 corrupt object test payload - deterministic content"
    expected_hash = hashlib.sha256(test_data).hexdigest()

    ref = put_artifact(
        data=test_data,
        artifact_id="m6_corrupt_test",
        mime_type="text/plain",
    )
    print(f"  Stored: {ref.artifact_id}")
    print(f"  Hash:   {ref.hash}")
    assert ref.hash == expected_hash

    # 2. Retrieve — must succeed
    print("\n[2] Retrieving (should succeed)...")
    retrieved = get_artifact(ref)
    assert retrieved == test_data
    print(f"  Retrieved {len(retrieved)} bytes, hash verified")
    passed += 1

    # 3. Corrupt the object in MinIO via boto3
    print("\n[3] Corrupting object in MinIO...")
    s3 = _make_s3_client()
    corrupt_data = b"CORRUPTED DATA - this is not the original"
    s3.put_object(
        Bucket=BUCKET,
        Key=ref.path.split("/" , 3)[3],
        Body=corrupt_data,
    )
    print(f"  Overwrote with {len(corrupt_data)} bytes")

    # 4. Retrieve corrupted — must raise hash mismatch
    print("\n[4] Retrieving corrupted object (should fail)...")
    try:
        get_artifact(ref)
        print("  FAIL: get_artifact did NOT raise on corrupted object")
        failed += 1
    except Exception as e:
        print(f"  Correctly raised: {type(e).__name__}: {e}")
        passed += 1

    # 5. Delete the object entirely
    print("\n[5] Deleting object from MinIO...")
    s3.delete_object(Bucket=BUCKET, Key=ref.path.split("/" , 3)[3])
    print(f"  Deleted: {ref.path}")

    # 6. Retrieve deleted — must raise
    print("\n[6] Retrieving deleted object (should fail)...")
    try:
        get_artifact(ref)
        print("  FAIL: get_artifact did NOT raise on missing object")
        failed += 1
    except Exception as e:
        print(f"  Correctly raised: {type(e).__name__}")
        passed += 1

    print("\n" + "=" * 60)
    print(f"RESULT: {passed} passed, {failed} failed")
    if failed == 0:
        print("M6 corrupt object test: PASS")
        return 0
    else:
        print("M6 corrupt object test: FAIL")
        return 1


if __name__ == "__main__":
    sys.exit(main())