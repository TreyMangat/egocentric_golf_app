"""Centralized configuration. Env-driven, no magic."""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache

from dotenv import load_dotenv

load_dotenv()


def _required(name: str) -> str:
    val = os.getenv(name)
    if not val:
        raise RuntimeError(f"Missing required env var: {name}")
    return val


_FALSY = {"", "0", "false", "no", "off"}


def _bool_env(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() not in _FALSY


@dataclass(frozen=True)
class AwsConfig:
    region: str
    bucket: str
    prefix_raw: str
    prefix_keypoints: str


@dataclass(frozen=True)
class MongoConfig:
    uri: str
    db: str


@dataclass(frozen=True)
class TemporalConfig:
    target: str
    namespace: str
    task_queue: str


@dataclass(frozen=True)
class Config:
    user_id: str
    pipeline_version: str
    log_level: str
    # When true, pose inference runs on local CPU via extract_pose_local
    # and the .npz is uploaded to S3 from the worker. When false, the
    # worker calls Modal's GPU function as in production. Defaults to
    # true so a fresh checkout is usable on a laptop without Modal.
    local_dev: bool
    aws: AwsConfig
    mongo: MongoConfig
    temporal: TemporalConfig


@lru_cache
def get_config() -> Config:
    return Config(
        user_id=os.getenv("USER_ID", "trey"),
        pipeline_version=os.getenv("PIPELINE_VERSION", "0.1.0"),
        log_level=os.getenv("LOG_LEVEL", "INFO"),
        local_dev=_bool_env("LOCAL_DEV", True),
        aws=AwsConfig(
            region=os.getenv("AWS_REGION", "us-east-1"),
            bucket=_required("S3_BUCKET"),
            prefix_raw=os.getenv("S3_PREFIX_RAW", "raw"),
            prefix_keypoints=os.getenv("S3_PREFIX_KEYPOINTS", "keypoints"),
        ),
        mongo=MongoConfig(
            uri=_required("MONGO_URI"),
            db=os.getenv("MONGO_DB", "golf_pipeline"),
        ),
        temporal=TemporalConfig(
            target=os.getenv("TEMPORAL_TARGET", "localhost:7233"),
            namespace=os.getenv("TEMPORAL_NAMESPACE", "default"),
            task_queue=os.getenv("TEMPORAL_TASK_QUEUE", "golf-pipeline"),
        ),
    )
