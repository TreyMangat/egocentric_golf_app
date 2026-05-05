"""Synthetic-pose tests for the metrics module — no video, no model dependency.

These exist so you can run `pytest` and trust the metrics math without needing
S3, Temporal, or Modal set up.

The synthetic poses below match the real BlazePose `pose_world_landmarks`
convention: meters, hip-centered, **+Y down** (head has the smallest y,
ankles the largest). `compute_all` is the load boundary that flips the
sign once so the rest of the metrics module can keep its documented +Y-up
semantics.
"""

from __future__ import annotations

import numpy as np
import pytest

from golf_pipeline.metrics.compute import (
    LHIP,
    LSH,
    LWR,
    NOSE,
    RHIP,
    RSH,
    compute_all,
)

LEL = 13
REL = 14
RWR = 16


def _addressed_pose() -> np.ndarray:
    """A single 33×4 BlazePose-world pose at address, in BlazePose's native
    +Y-down convention (head has the most negative y).
    """
    p = np.zeros((33, 4), dtype=np.float32)
    p[:, 3] = 1.0  # visibility
    p[NOSE, :3] = (0.0, -1.65, 0.0)
    # shoulders (left = +x, right = -x)
    p[LSH, :3] = (0.20, -1.45, 0.0)
    p[RSH, :3] = (-0.20, -1.45, 0.0)
    # elbows
    p[LEL, :3] = (0.20, -1.10, 0.05)
    p[REL, :3] = (-0.20, -1.10, 0.05)
    # wrists hang below shoulders → less negative y than shoulders, more
    # negative than hips (anatomically wrists are below shoulders, above
    # the floor; under +Y-down that's a y between shoulders' and feet's).
    p[LWR, :3] = (0.20, -0.85, 0.10)
    p[RWR, :3] = (-0.20, -0.85, 0.10)
    # hips
    p[LHIP, :3] = (0.10, -0.95, 0.0)
    p[RHIP, :3] = (-0.10, -0.95, 0.0)
    return p


def _synthetic_swing(fps: float = 60, duration_s: float = 2.5) -> tuple[np.ndarray, int]:
    """Build a deterministic 'swing' in BlazePose +Y-down coords. Lead
    wrist starts at address height (-0.85), climbs to the apex of the
    backswing (-2.0, well above the shoulders), drops through impact
    (-0.7), then rises back into the finish (-1.9). Returns
    `(kp, expected_top_frame)`.
    """
    n = int(duration_s * fps)
    base = _addressed_pose()
    kp = np.tile(base[None, :, :], (n, 1, 1))

    address_end = int(0.4 * fps)
    top_frame = int(1.4 * fps)
    impact_frame = int(1.7 * fps)
    finish_frame = int(2.4 * fps)

    # Backswing: lift wrist from y=-0.85 to y=-2.0 (above shoulders).
    for i in range(address_end, top_frame):
        u = (i - address_end) / max(1, (top_frame - address_end))
        kp[i, LWR, 1] = -0.85 + u * (-2.0 - (-0.85))

    # Downswing: drop wrist from -2.0 to ~-0.7 at impact (below address).
    for i in range(top_frame, impact_frame):
        u = (i - top_frame) / max(1, (impact_frame - top_frame))
        kp[i, LWR, 1] = -2.0 + u * (-0.7 - (-2.0))

    # Finish: rise to -1.9, settle.
    for i in range(impact_frame, n):
        u = min(1.0, (i - impact_frame) / max(1, (finish_frame - impact_frame)))
        kp[i, LWR, 1] = -0.7 + u * (-1.9 - (-0.7))

    return kp.astype(np.float32), top_frame


# ─── tests ────────────────────────────────────────────────────────────────────


def test_address_pose_metrics_are_zero_or_baseline():
    """Static pose → zero motion-derived metrics, zero spine tilt.

    Routes through `compute_all` (the load-boundary flip applies). Without
    the flip, `spineTiltAtAddressDeg` would come back as 180° instead of 0°.
    """
    pose = _addressed_pose()
    kp = np.tile(pose[None, :, :], (60, 1, 1)).astype(np.float32)

    _, metrics, _ = compute_all(kp, fps=60.0, lead_side="L", impact_frame=30)

    assert metrics.shoulder_turn_at_top_deg == pytest.approx(0.0, abs=0.5)
    assert metrics.hip_turn_at_top_deg == pytest.approx(0.0, abs=0.5)
    assert metrics.head_sway_max_mm == pytest.approx(0.0, abs=0.1)
    assert metrics.head_lift_max_mm == pytest.approx(0.0, abs=0.1)
    # Spine straight up → 0° tilt under +Y-up; without the load-time flip
    # this would be ~180°.
    assert metrics.spine_tilt_at_address_deg == pytest.approx(0.0, abs=1.0)


def test_phase_detection_finds_top_within_tolerance():
    """`compute_all` should locate the synthetic apex (highest hand) at the
    constructed `top_frame`. Without the load-time flip, `detect_phases`
    runs `argmax(lead_y)` against +Y-down data and lands on the *lowest*
    hand position, blowing this assertion.
    """
    kp, expected_top = _synthetic_swing()
    phases, _, _ = compute_all(kp, fps=60.0, lead_side="L")
    # Allow ±5 frames — synthetic data is sharp but the heuristic is
    # percentile-based.
    assert abs(phases.top.frame - expected_top) <= 5


def test_compute_all_produces_metrics_and_ranges():
    kp, _ = _synthetic_swing()
    _, metrics, ranges = compute_all(kp, fps=60.0, lead_side="L")

    # tempo should be backswing > downswing
    assert metrics.backswing_duration_ms is not None
    assert metrics.downswing_duration_ms is not None
    assert metrics.backswing_duration_ms > metrics.downswing_duration_ms

    # ranges populated for every target metric
    assert "tempoRatioBackswingDownswing" in ranges
    assert ranges["tempoRatioBackswingDownswing"].status in ("pass", "warn", "fail")


def test_shoulder_turn_90deg_under_y_down_input():
    """Regression: shoulders rotated 90° between address and the synthetic
    `top_frame` must produce `shoulderTurnAtTopDeg ≈ 90°`. Without the
    load-time flip, `detect_phases` picks the wrong `top` (lowest hand
    instead of highest), so the shoulder-turn measurement is taken at a
    near-address frame and reads ≈0°.
    """
    fps = 60.0
    n = int(2.5 * fps)
    base = _addressed_pose()
    kp = np.tile(base[None, :, :], (n, 1, 1)).astype(np.float32)

    address_end = int(0.4 * fps)
    top_frame = int(1.4 * fps)
    impact_frame = int(1.7 * fps)
    finish_frame = int(2.4 * fps)

    # Lead-wrist y trajectory (same shape as `_synthetic_swing`).
    for i in range(address_end, top_frame):
        u = (i - address_end) / max(1, (top_frame - address_end))
        kp[i, LWR, 1] = -0.85 + u * (-2.0 - (-0.85))
    for i in range(top_frame, impact_frame):
        u = (i - top_frame) / max(1, (impact_frame - top_frame))
        kp[i, LWR, 1] = -2.0 + u * (-0.7 - (-2.0))
    for i in range(impact_frame, n):
        u = min(1.0, (i - impact_frame) / max(1, (finish_frame - impact_frame)))
        kp[i, LWR, 1] = -0.7 + u * (-1.9 - (-0.7))

    # Rotate the shoulder line about the spine (Y) axis from 0° at
    # `address_end` to 90° at `top_frame`. R−L starts along +x at address;
    # after a 90° rotation about y it points along +z (or −z, depending on
    # sign convention) — either way the unsigned XZ-plane angle is 90°.
    sh_radius = abs(base[LSH, 0] - base[RSH, 0]) / 2  # 0.20
    sh_y = base[LSH, 1]  # shoulders share a y at rest
    for i in range(address_end, top_frame):
        u = (i - address_end) / max(1, (top_frame - address_end))
        theta = np.deg2rad(u * 90.0)
        c, s = np.cos(theta), np.sin(theta)
        kp[i, LSH, 0] = sh_radius * c
        kp[i, LSH, 2] = sh_radius * s
        kp[i, LSH, 1] = sh_y
        kp[i, RSH, 0] = -sh_radius * c
        kp[i, RSH, 2] = -sh_radius * s
        kp[i, RSH, 1] = sh_y
    # Hold the rotated shoulders for the rest of the clip — only address
    # vs top matters here.
    for i in range(top_frame, n):
        kp[i, LSH] = kp[top_frame - 1, LSH]
        kp[i, RSH] = kp[top_frame - 1, RSH]

    phases, metrics, _ = compute_all(kp, fps=fps, lead_side="L")

    # Sanity: phase detection must locate the synthetic apex.
    assert abs(phases.top.frame - top_frame) <= 5
    # Headline assertion: shoulder turn at the detected top reads ~90°,
    # not ~0° (broken-phase) or ~180° (sign flip).
    assert metrics.shoulder_turn_at_top_deg == pytest.approx(90.0, abs=5.0)


def test_top_clamped_before_impact_when_follow_through_apex_is_higher():
    """Regression: build a synthetic swing where the follow-through apex
    sits *higher* than the backswing apex. Without clamping the top
    search to `< impact_frame`, `argmax(lead_y)` picks the follow-through
    frame and reports `top` after `impact` (and a negative downswing).
    With the clamp, top must precede impact.
    """
    fps = 60.0
    n = int(3.0 * fps)
    base = _addressed_pose()
    kp = np.tile(base[None, :, :], (n, 1, 1)).astype(np.float32)

    # Mirror the real wedge clip's shape: impact lands earlier than midway,
    # follow-through apex is reached well *inside* the 0.7·n cap. Without
    # the new clamp, argmax over [takeaway, 0.7·n] picks the follow-through
    # apex; with the clamp, the search bound is `impact_frame`.
    address_end = int(0.4 * fps)
    backswing_top = int(1.0 * fps)   # backswing apex
    impact = int(1.2 * fps)          # earlier than mid-clip
    follow_apex = int(1.8 * fps)     # 108 frames; < 0.7·n = 126

    # Backswing climb: -0.85 → -1.8 (above shoulders).
    for i in range(address_end, backswing_top):
        u = (i - address_end) / max(1, (backswing_top - address_end))
        kp[i, LWR, 1] = -0.85 + u * (-1.8 - (-0.85))
    # Downswing: drop to -0.7 at impact.
    for i in range(backswing_top, impact):
        u = (i - backswing_top) / max(1, (impact - backswing_top))
        kp[i, LWR, 1] = -1.8 + u * (-0.7 - (-1.8))
    # Follow-through climb to a HIGHER apex (-2.2) at `follow_apex`,
    # then settle. -2.2 < -1.8 in raw coords, so after the load-flip it
    # becomes the *largest* lead-wrist y in the clip.
    for i in range(impact, follow_apex):
        u = (i - impact) / max(1, (follow_apex - impact))
        kp[i, LWR, 1] = -0.7 + u * (-2.2 - (-0.7))
    for i in range(follow_apex, n):
        kp[i, LWR, 1] = -2.2

    # Sanity-check the construction: follow-through apex must fall inside
    # the 0.7·n fallback window so the test actually exercises the bug
    # (otherwise the cap would mask it independent of the clamp).
    assert follow_apex < int(n * 0.7), (
        f"follow_apex={follow_apex} must be inside 0.7·n={int(n * 0.7)} "
        "for this test to exercise the clamp"
    )

    phases, metrics, _ = compute_all(kp, fps=fps, lead_side="L", impact_frame=impact)
    assert phases.top.frame < phases.impact.frame, (
        f"top={phases.top.frame} should precede impact={phases.impact.frame}; "
        "follow-through apex was picked instead of backswing apex."
    )
    assert abs(phases.top.frame - backswing_top) <= 5
    # And the downstream tempo math must come out positive.
    assert metrics.downswing_duration_ms is not None
    assert metrics.downswing_duration_ms > 0


def test_finish_caps_at_impact_plus_one_second_when_condition_unmet():
    """If the wrist never rises above the shoulder line after impact,
    `finish` must fall back to `impact + ~1s`, not the last frame of the
    clip. Without the cap, `head_excursions_mm` measures sway across all
    post-swing motion.
    """
    fps = 60.0
    n = int(4.0 * fps)  # 4-second clip — more than enough trailing frames
    base = _addressed_pose()
    kp = np.tile(base[None, :, :], (n, 1, 1)).astype(np.float32)

    address_end = int(0.4 * fps)
    top_frame = int(1.4 * fps)
    impact_frame = int(1.7 * fps)

    # Backswing → impact like the standard synthetic swing.
    for i in range(address_end, top_frame):
        u = (i - address_end) / max(1, (top_frame - address_end))
        kp[i, LWR, 1] = -0.85 + u * (-2.0 - (-0.85))
    for i in range(top_frame, impact_frame):
        u = (i - top_frame) / max(1, (impact_frame - top_frame))
        kp[i, LWR, 1] = -2.0 + u * (-0.7 - (-2.0))
    # After impact: hand stays *below* shoulders (no follow-through-up
    # motion), so the wrist-above-shoulder condition never fires.
    for i in range(impact_frame, n):
        kp[i, LWR, 1] = -0.7

    phases, _, _ = compute_all(kp, fps=fps, lead_side="L", impact_frame=impact_frame)
    expected_cap = impact_frame + int(1.0 * fps)
    assert phases.finish.frame == expected_cap
