"""Tests for `run_pose_inference`'s LOCAL_DEV / Modal routing.

The unit-level guarantee we care about: when `LOCAL_DEV=true` the activity
must never reach the Modal entrypoint. Anything that *would* call Modal
in this test is wired to a tripwire that raises on use.
"""

from __future__ import annotations

from pathlib import Path

import pytest


class _ModalTripWire:
    """Stand-in for `extract_pose`. Reaching `.remote.aio` is the failure
    mode: with LOCAL_DEV=true the activity must not call Modal at all.
    """

    def __init__(self) -> None:
        self.calls: list[dict] = []
        outer = self

        class _Remote:
            async def aio(self_inner, **kwargs):  # noqa: N805
                outer.calls.append(kwargs)
                raise AssertionError(
                    "extract_pose.remote.aio called with LOCAL_DEV=true"
                )

        self.remote = _Remote()


@pytest.fixture
def local_dev_env(monkeypatch):
    """Drive Config via env vars rather than mocking `get_config`, so every
    `from golf_pipeline.config import get_config` callsite (activities, s3,
    api, …) sees the same LOCAL_DEV=true config without per-module patches.
    """
    from golf_pipeline.config import get_config

    monkeypatch.setenv("LOCAL_DEV", "1")
    monkeypatch.setenv("S3_BUCKET", "test-bucket")
    monkeypatch.setenv("S3_PREFIX_RAW", "raw")
    monkeypatch.setenv("S3_PREFIX_KEYPOINTS", "kp")
    monkeypatch.setenv("MONGO_URI", "mongodb://test/")
    monkeypatch.setenv("MONGO_DB", "test")
    monkeypatch.setenv("USER_ID", "t")
    get_config.cache_clear()
    yield
    get_config.cache_clear()


@pytest.mark.asyncio
async def test_local_dev_uses_local_pose_and_skips_modal(local_dev_env, monkeypatch):
    from golf_pipeline.config import get_config
    from golf_pipeline.modal_pose import inference as inf
    from golf_pipeline.temporal import activities

    assert get_config().local_dev is True  # sanity: LOCAL_DEV env honored

    # Stub the local pose function — write a sentinel .npz so the upload step
    # has something to read.
    local_calls: list[tuple[str, str]] = []

    def fake_local(video_path: str, out_npz: str) -> dict:
        local_calls.append((video_path, out_npz))
        Path(out_npz).write_bytes(b"\x00" * 16)
        return {
            "fps": 60.0,
            "frames": 99,
            "schema": "blazepose-33",
            "model": "blazepose-full",
        }

    monkeypatch.setattr(inf, "extract_pose_local", fake_local)

    # Trip-wire the Modal entrypoint.
    tripwire = _ModalTripWire()
    monkeypatch.setattr(inf, "extract_pose", tripwire)

    # Stub S3 I/O so no real bucket is touched.
    monkeypatch.setattr(
        activities,
        "download_to_path",
        lambda key, path: Path(path).write_bytes(b"\x00" * 8),
    )
    uploaded: list[tuple[str, int, str]] = []

    def fake_upload(key, body, content_type="application/octet-stream"):
        size = len(body) if isinstance(body, (bytes, bytearray)) else 0
        uploaded.append((key, size, content_type))

    monkeypatch.setattr(activities, "upload_bytes", fake_upload)

    from temporalio.testing import ActivityEnvironment

    env = ActivityEnvironment()
    result = await env.run(
        activities.run_pose_inference,
        "s3://test-bucket/raw/t/sess/clip.mov",
        "sess",
        "t",
        "swing_001",
    )

    assert local_calls, "extract_pose_local was never called"
    assert not tripwire.calls, (
        f"Modal entrypoint reached despite LOCAL_DEV=true: {tripwire.calls}"
    )
    assert uploaded, "local-dev branch did not upload the .npz to S3"
    assert uploaded[0][0] == "kp/t/sess/swing_001.npz"
    assert result == {
        "fps": 60.0,
        "frames": 99,
        "schema": "blazepose-33",
        "keypoints_uri": "s3://test-bucket/kp/t/sess/swing_001.npz",
        "model": "blazepose-full",
    }

    # Lazy-import lock-in: if a future contributor hoists
    # `from golf_pipeline.modal_pose.inference import extract_pose` to module
    # top of activities.py, the symbol resolves at activities-import time
    # before this test's monkeypatch runs, and `inf.extract_pose` ends up
    # being the real Modal Function object instead of our tripwire. The
    # tripwire still catches `.remote.aio` calls — but the lazy-import
    # *intent* documented in activities.py would have silently regressed.
    # Pin that here.
    assert inf.extract_pose is tripwire, (
        "extract_pose was resolved before the test patched it — the lazy-"
        "import in run_pose_inference's not-local_dev branch was likely "
        "hoisted to module top. Restore the lazy import."
    )
