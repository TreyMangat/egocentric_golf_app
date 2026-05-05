"""Tests for GET /api/v1/swings/{id}/keypoints — the endpoint the SVG
skeleton overlay calls on the swing-detail page.
"""

from __future__ import annotations

import os

# The FastAPI app reads config at import time; set env before the import.
os.environ.setdefault("S3_BUCKET", "test-bucket")
os.environ.setdefault("MONGO_URI", "mongodb://test/")
os.environ.setdefault("MONGO_DB", "test")
os.environ.setdefault("USER_ID", "t")

from datetime import datetime  # noqa: E402
from pathlib import Path  # noqa: E402

import numpy as np  # noqa: E402
import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from golf_pipeline.api import server  # noqa: E402
from golf_pipeline.schemas import (  # noqa: E402
    Capture,
    Club,
    KeypointsRef,
    PhaseFrame,
    Phases,
    Swing,
    Tags,
    View,
)


@pytest.fixture
def client(monkeypatch):
    """Stub Mongo and S3 dependencies for the endpoint."""
    return TestClient(server.app)


def _swing_with_storage_ref(storage_ref: str | None) -> Swing:
    return Swing(
        _id="swing_test",
        userId="u",
        sessionId="s",
        createdAt=datetime(2026, 5, 4),
        status="accepted",
        motionScore=27.5,
        capture=Capture(
            view=View.DTL,
            club=Club.SEVEN_I,
            fps=60,
            resolution=(464, 832),
            phoneModel="iPhone16,2",
            videoKey="raw/u/s/swing_test.mov",
        ),
        tags=Tags(),
        phases=Phases(
            address=PhaseFrame(frame=0, tMs=0),
            takeaway=PhaseFrame(frame=24, tMs=400),
            top=PhaseFrame(frame=84, tMs=1400),
            transition=PhaseFrame(frame=85, tMs=1416),
            impact=PhaseFrame(frame=102, tMs=1700),
            finish=PhaseFrame(frame=132, tMs=2200),
        ),
        keypoints=(
            KeypointsRef(
                schema="blazepose-33-v2",
                fps=60,
                storageRef=storage_ref,
            )
            if storage_ref else None
        ),
    )


def _write_synthetic_npz(path: Path, frames: int = 5, fps: float = 60.0) -> None:
    """A small but realistic .npz: image array (frames, 33, 3) with one
    explicitly-NaN joint to verify NaN-safe JSON encoding survives the
    round trip."""
    image = np.zeros((frames, 33, 3), dtype=np.float32)
    image[..., 0] = 0.5  # x_norm centered
    image[..., 1] = 0.5  # y_norm centered
    image[..., 2] = 0.95  # visibility
    image[2, 7] = (np.nan, np.nan, np.nan)  # one missing joint mid-clip
    world = np.zeros((frames, 33, 4), dtype=np.float32)
    np.savez_compressed(path, keypoints_image=image, keypoints_world=world, fps=fps)


def test_keypoints_endpoint_returns_image_series_and_fps(client, monkeypatch, tmp_path):
    src = tmp_path / "kp.npz"
    _write_synthetic_npz(src, frames=5, fps=60.0)

    monkeypatch.setattr(
        server,
        "download_to_path",
        lambda key, dst: __import__("shutil").copyfile(src, dst),
    )

    async def fake_get_swing(swing_id: str):
        return _swing_with_storage_ref("s3://bucket/kp/u/s/swing_test.npz")

    monkeypatch.setattr(server, "get_swing", fake_get_swing)

    res = client.get("/api/v1/swings/swing_test/keypoints")
    assert res.status_code == 200
    body = res.json()
    assert body["swingId"] == "swing_test"
    assert body["schema"] == "blazepose-33-v2"
    assert body["fps"] == 60
    image = body["image"]
    assert len(image) == 5
    assert len(image[0]) == 33
    assert len(image[0][0]) == 3
    # NaNs survive as null (NaNSafeJSONResponse).
    assert image[2][7] == [None, None, None]
    # Cache header is conservative but present.
    assert "max-age" in res.headers.get("cache-control", "")


def test_keypoints_endpoint_returns_404_when_swing_missing(client, monkeypatch):
    async def fake_get_swing(swing_id: str):
        return None

    monkeypatch.setattr(server, "get_swing", fake_get_swing)
    res = client.get("/api/v1/swings/missing/keypoints")
    assert res.status_code == 404


def test_keypoints_endpoint_returns_404_when_no_offloaded_keypoints(client, monkeypatch):
    async def fake_get_swing(swing_id: str):
        return _swing_with_storage_ref(None)

    monkeypatch.setattr(server, "get_swing", fake_get_swing)
    res = client.get("/api/v1/swings/no_kp/keypoints")
    assert res.status_code == 404
    assert "no offloaded keypoints" in res.json()["detail"]
