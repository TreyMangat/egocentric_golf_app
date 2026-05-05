"""Tests for the wrist-motion gate that filters audio false positives."""

from __future__ import annotations

import shutil
from datetime import datetime
from pathlib import Path

import numpy as np
import pytest
from temporalio.testing import ActivityEnvironment

from golf_pipeline.schemas import (
    Capture,
    Club,
    Metrics,
    PhaseFrame,
    Phases,
    RangeStatus,
    Swing,
    SwingWindow,
    Tags,
    View,
)
from golf_pipeline.temporal import activities
from golf_pipeline.temporal.activities import (
    MOTION_SCORE_THRESHOLD_MS,
    compute_metrics_and_write,
    compute_motion_score,
)


def _pose_with_wrist_track(left_y: np.ndarray, right_y: np.ndarray | None = None) -> np.ndarray:
    n = len(left_y)
    kp = np.full((n, 33, 4), np.nan, dtype=np.float32)
    kp[:, 15, 0] = 0.0
    kp[:, 15, 1] = left_y
    kp[:, 15, 2] = 0.0
    kp[:, 15, 3] = 1.0

    if right_y is None:
        right_y = left_y
    kp[:, 16, 0] = 0.2
    kp[:, 16, 1] = right_y
    kp[:, 16, 2] = 0.0
    kp[:, 16, 3] = 1.0
    return kp


def test_compute_motion_score_separates_swing_from_standing_still():
    fps = 60.0
    duration_s = 3.0
    t = np.arange(int(duration_s * fps), dtype=np.float32) / fps
    impact_frame = int(1.5 * fps)

    swing_y = 2.0 * np.sin(2 * np.pi * 2.0 * t)
    swing_kp = _pose_with_wrist_track(swing_y)
    # Simulate a missing pose frame inside the scoring window; adjacent pairs
    # touching this frame should be skipped, not poison the score.
    swing_kp[impact_frame - 3, 15:17, :] = np.nan

    still_rng = np.random.default_rng(0)
    still_y = 0.85 + still_rng.normal(0.0, 0.0005, size=t.shape).astype(np.float32)
    still_kp = _pose_with_wrist_track(still_y)

    assert compute_motion_score(swing_kp, fps, impact_frame) > MOTION_SCORE_THRESHOLD_MS
    assert compute_motion_score(still_kp, fps, impact_frame) < 0.2


@pytest.fixture
def activity_env(monkeypatch):
    from golf_pipeline.config import get_config

    monkeypatch.setenv("LOCAL_DEV", "1")
    monkeypatch.setenv("S3_BUCKET", "test-bucket")
    monkeypatch.setenv("S3_PREFIX_RAW", "raw")
    monkeypatch.setenv("S3_PREFIX_KEYPOINTS", "kp")
    monkeypatch.setenv("MONGO_URI", "mongodb://test/")
    monkeypatch.setenv("MONGO_DB", "test")
    monkeypatch.setenv("USER_ID", "t")
    get_config.cache_clear()
    yield ActivityEnvironment()
    get_config.cache_clear()


def _write_npz(path: Path, kp: np.ndarray, fps: float) -> None:
    np.savez_compressed(
        path,
        keypoints_world=kp,
        keypoints_image=np.zeros((kp.shape[0], 33, 3)),
        fps=fps,
    )


def _window() -> SwingWindow:
    return SwingWindow(
        swing_id="swing_001",
        start_ms=0,
        end_ms=3000,
        impact_ms=1500,
        impact_confidence=0.9,
        club=Club.SEVEN_I,
        view=View.DTL,
    )


async def _run_write_activity(
    activity_env: ActivityEnvironment,
    monkeypatch,
    tmp_path: Path,
    kp: np.ndarray,
    *,
    expect_metrics: bool,
) -> Swing:
    src_npz = tmp_path / "kp.npz"
    _write_npz(src_npz, kp, 60.0)

    monkeypatch.setattr(
        activities,
        "download_to_path",
        lambda key, dst: shutil.copyfile(src_npz, dst),
    )

    inserted: list[Swing] = []

    async def fake_insert_swing(swing: Swing) -> str:
        inserted.append(swing)
        return swing.id

    async def fake_append_swing_to_session(session_id: str, swing_id: str) -> None:
        return None

    monkeypatch.setattr(activities, "insert_swing", fake_insert_swing)
    monkeypatch.setattr(activities, "append_swing_to_session", fake_append_swing_to_session)

    def fake_compute_all(kp_arg: np.ndarray, fps: float, impact_frame: int):
        if not expect_metrics:
            raise AssertionError("compute_all should not run for rejected swings")
        phases = Phases(
            address=PhaseFrame(frame=0, tMs=0),
            takeaway=PhaseFrame(frame=30, tMs=500),
            top=PhaseFrame(frame=60, tMs=1000),
            transition=PhaseFrame(frame=75, tMs=1250),
            impact=PhaseFrame(frame=90, tMs=1500),
            finish=PhaseFrame(frame=120, tMs=2000),
        )
        metrics = Metrics(tempoRatioBackswingDownswing=3.0)
        ranges = {
            "tempoRatioBackswingDownswing": RangeStatus(
                target=(2.5, 4.0),
                status="pass",
            )
        }
        return phases, metrics, ranges

    monkeypatch.setattr(activities, "compute_all", fake_compute_all)

    await activity_env.run(
        compute_metrics_and_write,
        "session_001",
        "user_001",
        _window(),
        "s3://test-bucket/kp/user_001/session_001/swing_001.npz",
        60.0,
    )

    assert len(inserted) == 1
    return inserted[0]


@pytest.mark.asyncio
async def test_compute_metrics_activity_rejects_low_motion(
    activity_env,
    monkeypatch,
    tmp_path,
):
    kp = _pose_with_wrist_track(np.full(180, 0.85, dtype=np.float32))

    swing = await _run_write_activity(
        activity_env,
        monkeypatch,
        tmp_path,
        kp,
        expect_metrics=False,
    )

    assert swing.status == "rejected"
    assert swing.motion_score < MOTION_SCORE_THRESHOLD_MS
    assert swing.phases is None
    assert swing.ranges == {}
    assert swing.metrics == Metrics()


@pytest.mark.asyncio
async def test_compute_metrics_activity_accepts_high_motion(
    activity_env,
    monkeypatch,
    tmp_path,
):
    fps = 60.0
    t = np.arange(180, dtype=np.float32) / fps
    kp = _pose_with_wrist_track(2.0 * np.sin(2 * np.pi * 2.0 * t))

    swing = await _run_write_activity(
        activity_env,
        monkeypatch,
        tmp_path,
        kp,
        expect_metrics=True,
    )

    assert swing.status == "accepted"
    assert swing.motion_score >= MOTION_SCORE_THRESHOLD_MS
    assert swing.phases is not None
    assert swing.metrics.tempo_ratio_backswing_downswing == 3.0
    assert "tempoRatioBackswingDownswing" in swing.ranges


def test_legacy_swing_docs_default_to_accepted():
    swing = Swing(
        _id="legacy",
        userId="user_001",
        sessionId="session_001",
        createdAt=datetime.utcnow(),
        capture=Capture(
            view=View.DTL,
            club=Club.SEVEN_I,
            fps=60,
            resolution=(464, 832),
            phoneModel="iPhone",
            videoKey="raw/user_001/session_001/swing.mov",
        ),
        tags=Tags(),
    )

    assert swing.status == "accepted"
    assert swing.motion_score == 0.0
