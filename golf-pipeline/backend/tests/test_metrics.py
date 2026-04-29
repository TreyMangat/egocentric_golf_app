"""Synthetic-pose tests for the metrics module — no video, no model dependency.

These exist so you can run `pytest` and trust the metrics math without needing
S3, Temporal, or Modal set up.
"""

from __future__ import annotations

import numpy as np
import pytest

from golf_pipeline.metrics.compute import (
    LSH,
    NOSE,
    RSH,
    compute_all,
    detect_phases,
    head_excursions_mm,
    shoulder_turn_deg,
    spine_tilt_deg,
)


def _addressed_pose() -> np.ndarray:
    """Return a single 33×4 pose at address: shoulders level on x-axis,
    pelvis below, nose at top, arms hanging down. Units are 'meters'."""
    p = np.zeros((33, 4), dtype=np.float32)
    p[:, 3] = 1.0  # visibility

    # nose
    p[NOSE, :3] = (0.0, 1.65, 0.0)
    # shoulders (left = +x, right = -x)
    p[LSH, :3] = (0.20, 1.45, 0.0)
    p[RSH, :3] = (-0.20, 1.45, 0.0)
    # elbows
    p[13, :3] = (0.20, 1.10, 0.05)  # left elbow
    p[14, :3] = (-0.20, 1.10, 0.05)
    # wrists
    p[15, :3] = (0.20, 0.85, 0.10)  # left wrist
    p[16, :3] = (-0.20, 0.85, 0.10)
    # hips
    p[23, :3] = (0.10, 0.95, 0.0)  # left hip
    p[24, :3] = (-0.10, 0.95, 0.0)
    return p


def _synthetic_swing(fps: float = 60, duration_s: float = 2.5) -> tuple[np.ndarray, int]:
    """Build a deterministic 'swing': hold address briefly, lift lead wrist
    to a top, drop it back through, finish high. Returns (kp, expected_top_frame).
    """
    n = int(duration_s * fps)
    base = _addressed_pose()
    kp = np.tile(base[None, :, :], (n, 1, 1))

    # Lead = left (index 15). Trajectory: address (0–0.4s), backswing (0.4–1.4s),
    # downswing (1.4–1.7s), follow-through (1.7–2.5s).
    lwr = 15
    address_end = int(0.4 * fps)
    top_frame = int(1.4 * fps)
    impact_frame = int(1.7 * fps)
    finish_frame = int(2.4 * fps)

    # Backswing: lift wrist from y=0.85 to y=2.0 (above shoulders)
    for i in range(address_end, top_frame):
        u = (i - address_end) / max(1, (top_frame - address_end))
        kp[i, lwr, 1] = 0.85 + u * (2.0 - 0.85)

    # Downswing: drop wrist from 2.0 down to ~0.7 at impact
    for i in range(top_frame, impact_frame):
        u = (i - top_frame) / max(1, (impact_frame - top_frame))
        kp[i, lwr, 1] = 2.0 + u * (0.7 - 2.0)

    # Finish: rise back up to 1.9, settle
    for i in range(impact_frame, n):
        u = min(1.0, (i - impact_frame) / max(1, (finish_frame - impact_frame)))
        kp[i, lwr, 1] = 0.7 + u * (1.9 - 0.7)

    return kp.astype(np.float32), top_frame


# ─── tests ────────────────────────────────────────────────────────────────────


def test_address_pose_metrics_are_zero_or_baseline():
    pose = _addressed_pose()
    kp = np.tile(pose[None, :, :], (10, 1, 1)).astype(np.float32)

    # No motion → no shoulder rotation
    assert shoulder_turn_deg(kp, 5) == pytest.approx(0.0, abs=0.01)
    # No head movement
    sway, lift = head_excursions_mm(kp, 0, 9)
    assert sway == pytest.approx(0.0, abs=0.01)
    assert lift == pytest.approx(0.0, abs=0.01)
    # Spine tilt: pelvis at y=0.95, shoulders at y=1.45 → spine is straight up
    tilt = spine_tilt_deg(kp, 5)
    assert tilt == pytest.approx(0.0, abs=1.0)


def test_phase_detection_finds_top_within_tolerance():
    kp, expected_top = _synthetic_swing()
    phases = detect_phases(kp, fps=60.0, lead_side="L")
    # allow ±5 frames of slack — synthetic data is sharp but we use percentile-based heuristics
    assert abs(phases.top.frame - expected_top) <= 5


def test_compute_all_produces_metrics_and_ranges():
    kp, _ = _synthetic_swing()
    phases, metrics, ranges = compute_all(kp, fps=60.0, lead_side="L")

    # tempo should be backswing > downswing
    assert metrics.backswing_duration_ms is not None
    assert metrics.downswing_duration_ms is not None
    assert metrics.backswing_duration_ms > metrics.downswing_duration_ms

    # ranges populated for every target metric
    assert "tempoRatioBackswingDownswing" in ranges
    assert ranges["tempoRatioBackswingDownswing"].status in ("pass", "warn", "fail")
