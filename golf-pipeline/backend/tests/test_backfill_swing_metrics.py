"""Tests for the metric-backfill maintenance script.

The script's I/O (S3 download, Mongo update) is bog-standard and mocked
elsewhere; what we actually want covered is `build_update_doc` — the pure
function that takes a stored swing + its keypoints and returns the `$set`
document. If `compute_all`'s output shape changes or the doc-building
loses a field, this test catches it.
"""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pytest

from golf_pipeline.schemas import (
    Capture,
    Club,
    KeypointsRef,
    Metrics,
    PhaseFrame,
    Phases,
    Pipeline,
    Swing,
    Tags,
    View,
)

# Scripts directory isn't on the package path; add it so we can import the
# module under test the same way the user runs it.
_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(_SCRIPTS))
from backfill_swing_metrics import build_update_doc  # noqa: E402, I001


# BlazePose joint indices.
NOSE = 0
LSH, RSH = 11, 12
LEL, REL = 13, 14
LWR, RWR = 15, 16
LHIP, RHIP = 23, 24


def _synthetic_swing_kp(fps: float = 60.0, duration_s: float = 2.5) -> tuple[np.ndarray, int]:
    """Reuse the +Y-down synthetic swing shape from test_metrics.py.

    Lead-wrist trajectory: address → top (above shoulders) → impact
    (around address height) → finish.
    """
    n = int(duration_s * fps)
    base = np.zeros((33, 4), dtype=np.float32)
    base[:, 3] = 1.0
    base[NOSE, :3] = (0.0, -1.65, 0.0)
    base[LSH, :3] = (0.20, -1.45, 0.0)
    base[RSH, :3] = (-0.20, -1.45, 0.0)
    base[LEL, :3] = (0.20, -1.10, 0.05)
    base[REL, :3] = (-0.20, -1.10, 0.05)
    base[LWR, :3] = (0.20, -0.85, 0.10)
    base[RWR, :3] = (-0.20, -0.85, 0.10)
    base[LHIP, :3] = (0.10, -0.95, 0.0)
    base[RHIP, :3] = (-0.10, -0.95, 0.0)

    kp = np.tile(base[None, :, :], (n, 1, 1))
    address_end = int(0.4 * fps)
    top_frame = int(1.4 * fps)
    impact_frame = int(1.7 * fps)
    finish_frame = int(2.4 * fps)

    for i in range(address_end, top_frame):
        u = (i - address_end) / max(1, (top_frame - address_end))
        kp[i, LWR, 1] = -0.85 + u * (-2.0 - (-0.85))
    for i in range(top_frame, impact_frame):
        u = (i - top_frame) / max(1, (impact_frame - top_frame))
        kp[i, LWR, 1] = -2.0 + u * (-0.7 - (-2.0))
    for i in range(impact_frame, n):
        u = min(1.0, (i - impact_frame) / max(1, (finish_frame - impact_frame)))
        kp[i, LWR, 1] = -0.7 + u * (-1.9 - (-0.7))

    return kp.astype(np.float32), impact_frame


def _stored_swing_with_broken_metrics(impact_frame: int) -> Swing:
    """Approximate a pre-fix stored doc: phases are populated (status=
    accepted) but metric values are stand-ins. We only care about the
    fields the backfill consumes: phases.impact.frame and the swing
    metadata that round-trips through the update."""
    return Swing(
        _id="swing_test_backfill",
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
            videoKey="raw/u/s/swing_test_backfill.mov",
        ),
        tags=Tags(),
        phases=Phases(
            address=PhaseFrame(frame=0, tMs=0),
            takeaway=PhaseFrame(frame=24, tMs=400),
            top=PhaseFrame(frame=110, tMs=1833),  # bogus — backfill should fix
            transition=PhaseFrame(frame=111, tMs=1850),
            impact=PhaseFrame(frame=impact_frame, tMs=int(impact_frame / 60 * 1000)),
            finish=PhaseFrame(frame=149, tMs=2483),
        ),
        metrics=Metrics(
            spineTiltAtAddressDeg=140.0,  # the y-flip bug value
            shoulderTurnAtTopDeg=2.0,
        ),
        keypoints=KeypointsRef(
            schema="blazepose-33-v2",
            fps=60,
            storageRef="s3://bucket/kp/u/s/swing_test_backfill.npz",
        ),
        pipeline=Pipeline(version="0.1.0", poseModel="blazepose-full-v1"),
    )


def test_build_update_doc_overwrites_broken_metrics_with_current_compute_all():
    kp, impact_frame = _synthetic_swing_kp()
    stored = _stored_swing_with_broken_metrics(impact_frame)

    update = build_update_doc(stored, kp, fps=60.0, pipeline_version="0.1.2")

    # Update set must carry the four metric-related fields and nothing else
    # that would clobber capture / keypoints / etc.
    assert set(update.keys()) == {
        "phases", "metrics", "ranges",
        "pipeline.version", "pipeline.backfilledAt",
    }

    # Spine tilt must come back into the +Y-up regime (< 90°) — i.e., the
    # post-fix formula was actually exercised, not the stored stub.
    assert update["metrics"]["spineTiltAtAddressDeg"] is not None
    assert update["metrics"]["spineTiltAtAddressDeg"] < 90.0

    # Phases must be a dict-shaped Phases payload (Mongo serialization).
    assert update["phases"]["impact"]["frame"] == impact_frame

    # Ranges is a dict from metric name → status; non-empty and serialized.
    assert "tempoRatioBackswingDownswing" in update["ranges"]
    assert update["ranges"]["tempoRatioBackswingDownswing"]["status"] in (
        "pass", "warn", "fail",
    )

    # Pipeline version was bumped.
    assert update["pipeline.version"] == "0.1.2"
    assert isinstance(update["pipeline.backfilledAt"], datetime)


def test_build_update_doc_refuses_swings_without_phases():
    kp, _ = _synthetic_swing_kp()
    stored = _stored_swing_with_broken_metrics(impact_frame=102)
    stored = stored.model_copy(update={"phases": None})

    with pytest.raises(ValueError, match="no stored phases"):
        build_update_doc(stored, kp, fps=60.0, pipeline_version="0.1.2")
