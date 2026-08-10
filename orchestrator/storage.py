from __future__ import annotations

import hashlib
import os
from typing import Tuple
from urllib.parse import urlparse

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

from contracts.common.envelope import ArtifactRefV1

BUCKET = os.environ.get("ARTIFACT_BUCKET", "avatar-harness-poc")
S3_ENDPOINT_URL = os.environ.get("S3_ENDPOINT_URL", "http://localhost:9000")
S3_ACCESS_KEY_ID = os.environ.get("S3_ACCESS_KEY_ID", "minioadmin")
S3_SECRET_ACCESS_KEY = os.environ.get("S3_SECRET_ACCESS_KEY", "minioadmin")
S3_REGION_NAME = os.environ.get("S3_REGION_NAME", "us-east-1")


def _make_s3_client():
    config = Config(signature_version="s3v4")
    return boto3.client(
        "s3",
        endpoint_url=S3_ENDPOINT_URL,
        aws_access_key_id=S3_ACCESS_KEY_ID,
        aws_secret_access_key=S3_SECRET_ACCESS_KEY,
        region_name=S3_REGION_NAME,
        config=config,
    )


def _bucket_and_key_from_path(path: str) -> Tuple[str, str]:
    parsed = urlparse(path)
    if parsed.scheme != "s3":
        raise ValueError(f"Unsupported artifact path scheme: {parsed.scheme}")
    bucket = parsed.netloc
    key = parsed.path.lstrip("/")
    if not bucket or not key:
        raise ValueError(f"Invalid S3 artifact path: {path}")
    return bucket, key


def _ensure_bucket_exists(client) -> None:
    try:
        client.head_bucket(Bucket=BUCKET)
    except ClientError as exc:
        error_code = exc.response.get("Error", {}).get("Code")
        if error_code in {"404", "NoSuchBucket", "403", "Forbidden"}:
            client.create_bucket(Bucket=BUCKET)
        else:
            raise
    except Exception:
        # MinIO stack unavailable locally (e.g. unit test environment without Docker)
        pass


def put_artifact(data: bytes, artifact_id: str, mime_type: str) -> ArtifactRefV1:
    digest = hashlib.sha256(data).hexdigest()
    key = f"artifacts/{artifact_id}_{digest}"

    client = _make_s3_client()
    try:
        _ensure_bucket_exists(client)
        client.put_object(
            Bucket=BUCKET,
            Key=key,
            Body=data,
            ContentType=mime_type,
        )
    except Exception as exc:
        raise RuntimeError(f"Failed to put artifact {artifact_id} to {BUCKET}/{key}: {exc}") from exc

    return ArtifactRefV1(
        artifact_id=artifact_id,
        path=f"s3://{BUCKET}/{key}",
        hash=digest,
        mime_type=mime_type,
    )


def get_artifact(ref: ArtifactRefV1) -> bytes:
    bucket, key = _bucket_and_key_from_path(ref.path)
    client = _make_s3_client()
    response = client.get_object(Bucket=bucket, Key=key)
    body = response["Body"].read()
    actual_hash = hashlib.sha256(body).hexdigest()
    if actual_hash != ref.hash:
        raise ValueError(
            f"Stored artifact hash mismatch: expected {ref.hash}, got {actual_hash}"
        )
    return body
