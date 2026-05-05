"""Tier 1 deterministic metrics computed from a pose timeseries.

All formulas operate on the BlazePose-33 keypoint set. Joint indices follow
the MediaPipe spec:
  0  nose
  11 left shoulder    12 right shoulder
  13 left elbow       14 right elbow
  15 left wrist       16 right wrist
  23 left hip         24 right hip
  25 left knee        26 right knee
  27 left ankle       28 right ankle

Conventions
-----------
- Internal formulas in this module assume world coords with **+Y up**:
  meters, pelvis-centered, head at the largest y. BlazePose's
  `pose_world_landmarks` actually ships +Y *down* (head at the smallest y).
  The single load-boundary fix lives at the top of `compute_all` — every
  helper below (detect_phases, shoulder_turn_deg, spine_tilt_deg, …) is
  written against +Y up and trusts the caller to have come through
  `compute_all`. `.npz` files on S3 stay canonical BlazePose output; we do
  not rewrite them.
- `lead` = side closest to the target. For a right-handed golfer this is
  left. Set `lead_side="L"` (default) or `"R"` per swing.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import numpy as np

from golf_pipeline.schemas import Metrics, PhaseFrame, Phases, RangeStatus

# joint indices
NOSE = 0
LSH, RSH = 11, 12
LEL, REL = 13, 14
LWR, RWR = 15, 16
LHIP, RHIP = 23, 24

LeadSide = Literal["L", "R"]


# ─── helpers ──────────────────────────────────────────────────────────────────


def _angle_deg_2d(v1: np.ndarray, v2: np.ndarray) -> float:
    """Unsigned angle between two 2D vectors, in degrees."""
    n1 = np.linalg.norm(v1) + 1e-9
    n2 = np.linalg.norm(v2) + 1e-9
    cos = float(np.clip(np.dot(v1, v2) / (n1 * n2), -1.0, 1.0))
    return float(np.degrees(np.arccos(cos)))


def _signed_angle_xz_deg(v: np.ndarray) -> float:
    """Signed angle of vector v in the XZ plane (camera-up plane in BlazePose)."""
    return float(np.degrees(np.arctan2(v[2], v[0])))


def _wrist_speed(kp: np.ndarray, lead_idx: int, fps: float) -> np.ndarray:
    """Per-frame wrist speed (units/s) using forward differences."""
    pos = kp[:, lead_idx, :3]
    diff = np.diff(pos, axis=0, prepend=pos[:1])
    return np.linalg.norm(diff, axis=1) * fps


# ─── phase detection ───────────────────────────────────────────────────────────


def detect_phases(
    kp: np.ndarray,
    fps: float,
    lead_side: LeadSide = "L",
    impact_frame: int | None = None,
) -> Phases:
    """Detect 6 swing phases from pose. `impact_frame` should be supplied by
    the audio segmenter when available — pose alone is unreliable at impact.
    """
    n = kp.shape[0]
    lead_wrist = LWR if lead_side == "L" else RWR
    speed = _wrist_speed(kp, lead_wrist, fps)

    # address: first frame where speed below threshold for >= 100ms
    speed_thresh = float(np.nanpercentile(speed, 25)) * 1.2
    win = max(1, int(0.1 * fps))
    address_frame = 0
    for i in range(n - win):
        if np.all(speed[i : i + win] < speed_thresh):
            address_frame = i
            break

    # takeaway: first frame after address with speed > 2× threshold
    takeaway_frame = address_frame
    for i in range(address_frame + 1, n):
        if speed[i] > speed_thresh * 2:
            takeaway_frame = i
            break

    # top: argmax of lead-wrist height between takeaway and impact.
    # Real swings have two y-apexes — top of backswing AND follow-through
    # (lead arm crossing up over the body). Without clamping the search
    # to end at `impact_frame`, argmax sometimes picks the follow-through
    # apex on driver/wedge clips, producing a "top" that's *after* impact
    # and a negative downswing duration. Audio impact is the canonical
    # phase anchor in V1 (see PROJECT_SPEC.md); using it as the search
    # bound is the same primitive doing more work. The `0.7·n` cap is
    # retained as a defensive fallback if impact_frame is ever absent
    # (e.g. a future audio-optional path).
    if impact_frame is not None:
        search_end = max(takeaway_frame + 1, min(impact_frame, int(n * 0.7)))
    else:
        search_end = max(takeaway_frame + 1, int(n * 0.7))
    heights = kp[takeaway_frame:search_end, lead_wrist, 1]  # +y up
    top_offset = int(np.argmax(heights))
    top_frame = takeaway_frame + top_offset

    # transition: first frame after top where wrist y starts decreasing
    transition_frame = top_frame
    for i in range(top_frame + 1, n):
        if kp[i, lead_wrist, 1] < kp[i - 1, lead_wrist, 1]:
            transition_frame = i
            break

    # impact: prefer audio-anchored; fallback = peak wrist speed after transition
    if impact_frame is None:
        impact_frame = int(transition_frame + np.argmax(speed[transition_frame:]))

    # finish: first stable frame after impact with wrist height > shoulder.
    # Real-swing fallback: if the wrist-above-shoulder + speed-stable
    # condition never fires inside the clip (segmenter cut short, follow-
    # through truncated, pose noise on the trailing wrist) we cap finish
    # at impact + ~1 s rather than letting it slide to the last frame of
    # the clip. The previous `n - 1` fallback inflated `head_excursions_mm`
    # by including all post-swing motion in the sway window — see
    # docs/diagnose_swing_20260504_205932_2a478c_swing_000.md (376 mm sway
    # vs. 68 mm at the audio-anchored impact frame).
    sh_y = (kp[:, LSH, 1] + kp[:, RSH, 1]) / 2
    follow_through_cap = min(impact_frame + int(1.0 * fps), n - 1)
    finish_frame = follow_through_cap
    for i in range(impact_frame + 1, n - win):
        if (
            kp[i, lead_wrist, 1] > sh_y[i]
            and np.all(speed[i : i + win] < speed_thresh)
        ):
            finish_frame = i
            break

    def pf(f: int) -> PhaseFrame:
        return PhaseFrame(frame=int(f), tMs=int(f / fps * 1000))

    return Phases(
        address=pf(address_frame),
        takeaway=pf(takeaway_frame),
        top=pf(top_frame),
        transition=pf(transition_frame),
        impact=pf(impact_frame),
        finish=pf(finish_frame),
    )


# ─── individual metrics ────────────────────────────────────────────────────────


def tempo(phases: Phases) -> tuple[float, int, int]:
    backswing_ms = phases.top.t_ms - phases.address.t_ms
    downswing_ms = phases.impact.t_ms - phases.top.t_ms
    if downswing_ms <= 0:
        return float("nan"), backswing_ms, downswing_ms
    return backswing_ms / downswing_ms, backswing_ms, downswing_ms


def shoulder_turn_deg(kp: np.ndarray, address_frame: int, frame: int) -> float:
    """Angle of the shoulder line at `frame` relative to its position at the
    *detected* address frame. Audio-cut clips don't always start exactly at
    address, so we anchor on the address frame the phase detector found
    rather than on `kp[0]`.
    """
    sh0 = kp[address_frame, RSH, [0, 2]] - kp[address_frame, LSH, [0, 2]]  # XZ plane
    sht = kp[frame, RSH, [0, 2]] - kp[frame, LSH, [0, 2]]
    return _angle_deg_2d(sh0, sht)


def hip_turn_deg(kp: np.ndarray, address_frame: int, frame: int) -> float:
    h0 = kp[address_frame, RHIP, [0, 2]] - kp[address_frame, LHIP, [0, 2]]
    ht = kp[frame, RHIP, [0, 2]] - kp[frame, LHIP, [0, 2]]
    return _angle_deg_2d(h0, ht)


def head_displacement_mm(kp: np.ndarray, frame: int) -> tuple[float, float]:
    """Lateral and vertical displacement of nose vs frame 0, in mm.
    BlazePose world units are meters, so multiply by 1000.
    """
    d = kp[frame, NOSE] - kp[0, NOSE]
    return abs(float(d[0]) * 1000), float(d[1]) * 1000


def head_excursions_mm(
    kp: np.ndarray,
    address_frame: int,
    finish_frame: int,
) -> tuple[float, float]:
    """Max lateral sway and vertical lift across the swing window."""
    rel = kp[address_frame : finish_frame + 1, NOSE] - kp[address_frame, NOSE]
    sway = float(np.nanmax(np.abs(rel[:, 0]))) * 1000
    lift = float(np.nanmax(rel[:, 1])) * 1000
    return sway, lift


def spine_tilt_deg(kp: np.ndarray, frame: int) -> float:
    """Angle of pelvis→shoulders vector vs vertical."""
    pelvis = (kp[frame, LHIP, :3] + kp[frame, RHIP, :3]) / 2
    shoulders = (kp[frame, LSH, :3] + kp[frame, RSH, :3]) / 2
    spine = shoulders - pelvis
    vertical = np.array([0.0, 1.0, 0.0])
    return _angle_deg_2d(spine, vertical)


def lead_arm_angle_deg(kp: np.ndarray, frame: int, lead_side: LeadSide) -> float:
    sh = LSH if lead_side == "L" else RSH
    el = LEL if lead_side == "L" else REL
    wr = LWR if lead_side == "L" else RWR
    upper = kp[frame, sh, :3] - kp[frame, el, :3]
    fore = kp[frame, wr, :3] - kp[frame, el, :3]
    return 180.0 - _angle_deg_2d(upper, fore)  # straight = 180°


def wrist_hinge_max_deg(
    kp: np.ndarray, address_frame: int, top_frame: int, lead_side: LeadSide
) -> float:
    """Approximation: angle between forearm and a 'club proxy' vector,
    where the club proxy is the lead-hand wrist→nose vector projected.
    This is a rough V1 stand-in until we track the club explicitly.
    """
    el = LEL if lead_side == "L" else REL
    wr = LWR if lead_side == "L" else RWR
    angles = []
    for f in range(address_frame, top_frame + 1):
        forearm = kp[f, wr, :3] - kp[f, el, :3]
        # naive club proxy: lead wrist → nose direction, projected
        club = kp[f, NOSE, :3] - kp[f, wr, :3]
        angles.append(_angle_deg_2d(forearm, club))
    return float(np.nanmax(angles)) if angles else float("nan")


# ─── aggregate ─────────────────────────────────────────────────────────────────


@dataclass
class TargetRange:
    lo: float
    hi: float

    def status_for(self, value: float | None, *, warn_pad: float = 0.15) -> RangeStatus:
        if value is None or np.isnan(value):
            return RangeStatus(target=(self.lo, self.hi), status="warn")
        pad = (self.hi - self.lo) * warn_pad
        if self.lo <= value <= self.hi:
            return RangeStatus(target=(self.lo, self.hi), status="pass")
        if (self.lo - pad) <= value <= (self.hi + pad):
            return RangeStatus(target=(self.lo, self.hi), status="warn")
        return RangeStatus(target=(self.lo, self.hi), status="fail")


# Targets sourced from common golf biomechanics references (V1 placeholders —
# refine after collecting your own baseline of 50+ swings).
TARGETS: dict[str, TargetRange] = {
    "tempoRatioBackswingDownswing": TargetRange(2.8, 3.2),
    "shoulderTurnAtTopDeg": TargetRange(80, 105),
    "hipTurnAtTopDeg": TargetRange(35, 55),
    "xFactorDeg": TargetRange(35, 55),
    "wristHingeMaxDeg": TargetRange(80, 95),
    "headSwayMaxMm": TargetRange(0, 50),
    "headLiftMaxMm": TargetRange(0, 30),
    "spineTiltAtAddressDeg": TargetRange(28, 38),
    "leadArmAngleAtTopDeg": TargetRange(160, 180),
}


def compute_all(
    kp: np.ndarray,
    fps: float,
    lead_side: LeadSide = "L",
    impact_frame: int | None = None,
) -> tuple[Phases, Metrics, dict[str, RangeStatus]]:
    # Load-boundary y-axis flip: BlazePose `pose_world_landmarks` ships
    # +Y down, but every helper below is written against +Y up (head at
    # the largest y). One transformation here, no flips elsewhere — see
    # the module docstring. Copy first so we never mutate the caller's
    # array (the activity reuses `kp` to compute `motion_score`).
    kp = kp.copy()
    kp[..., 1] = -kp[..., 1]

    phases = detect_phases(kp, fps, lead_side=lead_side, impact_frame=impact_frame)

    ratio, backswing_ms, downswing_ms = tempo(phases)
    sh_top = shoulder_turn_deg(kp, phases.address.frame, phases.top.frame)
    hip_top = hip_turn_deg(kp, phases.address.frame, phases.top.frame)
    sway, lift = head_excursions_mm(kp, phases.address.frame, phases.finish.frame)

    metrics = Metrics(
        tempoRatioBackswingDownswing=None if np.isnan(ratio) else round(ratio, 2),
        backswingDurationMs=backswing_ms,
        downswingDurationMs=downswing_ms,
        shoulderTurnAtTopDeg=round(sh_top, 1),
        hipTurnAtTopDeg=round(hip_top, 1),
        xFactorDeg=round(sh_top - hip_top, 1),
        wristHingeMaxDeg=round(
            wrist_hinge_max_deg(kp, phases.address.frame, phases.top.frame, lead_side), 1
        ),
        headSwayMaxMm=round(sway, 1),
        headLiftMaxMm=round(lift, 1),
        spineTiltAtAddressDeg=round(spine_tilt_deg(kp, phases.address.frame), 1),
        spineTiltAtImpactDeg=round(spine_tilt_deg(kp, phases.impact.frame), 1),
        leadArmAngleAtTopDeg=round(lead_arm_angle_deg(kp, phases.top.frame, lead_side), 1),
    )

    ranges: dict[str, RangeStatus] = {}
    m = metrics.model_dump(by_alias=True)
    for key, target in TARGETS.items():
        ranges[key] = target.status_for(m.get(key))

    return phases, metrics, ranges
