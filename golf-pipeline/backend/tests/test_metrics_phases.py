"""Phase-detection tests against captured real-swing speed signals.

The fixture stores only the lead-wrist 3D speed series for swing_003
(`tests/fixtures/swing_003_lead_wrist_speed.npy`, extracted from the
Step 1 takeaway diagnostic). The test synthesizes a minimal `kp`
array — lead-wrist position integrated from speed, all other joints
held at a static zero pose — so `_wrist_speed(kp, LWR, fps)` reproduces
the captured signal exactly. This exercises the speed-driven phase
rules (address, takeaway) on a real-world signal without checking in
a full-pose `.npz`.

Other phase rules (top apex, transition, finish) read joint *positions*
the synthesis doesn't model and so are not exercised here.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

from golf_pipeline.metrics.compute import LWR, detect_phases

FIXTURE_DIR = Path(__file__).parent / "fixtures"


def _kp_from_lead_wrist_speed(speed: np.ndarray, fps: float) -> np.ndarray:
    """Build a kp array whose `_wrist_speed(kp, LWR, fps)` returns `speed`.

    Lead-wrist position advances along +x by `speed[i]/fps` per frame.
    NaN frames in `speed` propagate as NaN positions; once NaN appears
    every later position stays NaN. That matches the swing_003 fixture,
    where NaN frames form a single contiguous tail block (frames 420+).
    """
    n = len(speed)
    kp = np.zeros((n, 33, 4), dtype=np.float64)
    kp[..., 3] = 1.0  # visibility
    pos = np.zeros((n, 3), dtype=np.float64)
    nan_seen = False
    for i in range(1, n):
        s = float(speed[i])
        if np.isnan(s) or nan_seen:
            pos[i] = np.nan
            nan_seen = True
        else:
            pos[i] = pos[i - 1] + np.array([s / fps, 0.0, 0.0])
    kp[:, LWR, :3] = pos
    return kp


def test_takeaway_lands_after_waggle_on_swing_003():
    """Real-swing regression: swing_003's lead-wrist speed series must
    drive the takeaway detector past several sub-second waggle bursts
    in the pre-swing region and onto the actual swing initiation.

    The Step 1 diagnostic established for this clip:
      - audio impact at fragment frame 300 (~5.0 s)
      - real sustained wrist-motion-onset at f ≈ 249 (~4.15 s)
      - waggle bursts at f≈45 (3-frame, ~750 ms) and f≈96 (~1600 ms)

    With 3-frame smoothing and ≥200 ms persistence, takeaway must
    land in [150, 290] — well past the waggle, with a plausible
    backswing duration before impact.
    """
    speed = np.load(FIXTURE_DIR / "swing_003_lead_wrist_speed.npy")
    fps = 59.972985141828005
    impact_frame = 300

    kp = _kp_from_lead_wrist_speed(speed, fps)
    phases = detect_phases(kp, fps=fps, lead_side="L", impact_frame=impact_frame)

    assert 150 <= phases.takeaway.frame <= 290, (
        f"takeaway={phases.takeaway.frame} should land in [150, 290] "
        "(real-swing initiation region, post-waggle)"
    )
    assert phases.takeaway.frame > 30, (
        f"takeaway={phases.takeaway.frame} indicates a single-frame spike "
        "trigger — persistence rule missing or too short"
    )
    assert phases.takeaway.frame < impact_frame, (
        f"takeaway={phases.takeaway.frame} must precede impact={impact_frame}"
    )
