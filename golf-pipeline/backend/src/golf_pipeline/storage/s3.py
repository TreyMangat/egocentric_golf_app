"""S3 client wrapper. Presigned URLs for upload, key conventions, basic ops."""

from __future__ import annotations

import io
from typing import BinaryIO

import boto3
from botocore.client import Config as BotoConfig

from golf_pipeline.config import get_config

_S3_SESSION = None


def s3_client():
    global _S3_SESSION
    if _S3_SESSION is None:
        cfg = get_config()
        _S3_SESSION = boto3.client(
            "s3",
            region_name=cfg.aws.region,
            endpoint_url=f"https://s3.{cfg.aws.region}.amazonaws.com",
            config=BotoConfig(signature_version="s3v4"),
        )
    return _S3_SESSION


def parse_s3_uri(uri: str) -> tuple[str, str]:
    """Split an `s3://bucket/key` URI into `(bucket, key)`.

    Single source of truth for this so callers don't reach for ad-hoc
    `uri.split("/", 3)[-1]` slicing that mis-parses on malformed input.
    """
    if not uri.startswith("s3://"):
        raise ValueError(f"expected s3:// uri, got {uri!r}")
    rest = uri[len("s3://") :]
    bucket, _, key = rest.partition("/")
    if not bucket or not key:
        raise ValueError(f"s3 uri missing bucket or key: {uri!r}")
    return bucket, key


def raw_video_key(user_id: str, session_id: str, clip_id: str) -> str:
    cfg = get_config()
    return f"{cfg.aws.prefix_raw}/{user_id}/{session_id}/{clip_id}.mov"


def keypoints_key(user_id: str, session_id: str, swing_id: str) -> str:
    cfg = get_config()
    return f"{cfg.aws.prefix_keypoints}/{user_id}/{session_id}/{swing_id}.npz"


def presign_put(
    key: str,
    content_type: str = "video/quicktime",
    expires_seconds: int = 3600,
) -> str:
    """Generate a presigned PUT URL for the iOS app to upload directly to S3."""
    cfg = get_config()
    return s3_client().generate_presigned_url(
        "put_object",
        Params={"Bucket": cfg.aws.bucket, "Key": key, "ContentType": content_type},
        ExpiresIn=expires_seconds,
    )


def presign_get(key: str, expires_seconds: int = 3600) -> str:
    cfg = get_config()
    return s3_client().generate_presigned_url(
        "get_object",
        Params={"Bucket": cfg.aws.bucket, "Key": key},
        ExpiresIn=expires_seconds,
    )


def upload_bytes(key: str, body: bytes | BinaryIO, content_type: str = "application/octet-stream"):
    cfg = get_config()
    if isinstance(body, bytes):
        body = io.BytesIO(body)
    s3_client().upload_fileobj(
        body, cfg.aws.bucket, key, ExtraArgs={"ContentType": content_type}
    )


def download_to_path(key: str, local_path: str):
    cfg = get_config()
    s3_client().download_file(cfg.aws.bucket, key, local_path)
