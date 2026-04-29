"""Synthetic-audio test for the impact segmenter.

The synthesizer plants a known set of impacts and a known set of distractor
whooshes; we feed the resulting wav through `detect_impacts` at its default
settings and check three things:

  - every planted impact is recovered within ±50 ms,
  - no detection lands inside a distractor window,
  - no detection lands in a silent region.

The audio_impact module is intentionally NOT tuned to make this pass.
Synthetic data is more discriminable than real range audio, so calibrating
threshold/band edges to it would set them tighter than reality warrants.
Real calibration happens against a real range recording.
"""

from __future__ import annotations

import sys
from pathlib import Path

# scripts/ isn't on the package path; we import the synthesizer from there.
_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(_SCRIPTS))

import synth_impacts  # noqa: E402

from golf_pipeline.segmentation.audio_impact import detect_impacts  # noqa: E402


TIMING_TOLERANCE_MS = 50
DISTRACTOR_GUARD_MS = 200


def _classify(t_ms: int, gt: synth_impacts.GroundTruth) -> str:
    nearest_imp = min((abs(t_ms - im) for im in gt.impacts_ms), default=10**9)
    nearest_dis = min((abs(t_ms - dm) for dm in gt.distractors_ms), default=10**9)
    if nearest_imp <= TIMING_TOLERANCE_MS:
        return "impact"
    if nearest_dis <= DISTRACTOR_GUARD_MS:
        return "distractor"
    return "silent"


def test_default_segmenter_recovers_planted_impacts(tmp_path):
    wav = tmp_path / "fake_range.wav"
    gt_json = tmp_path / "fake_range.gt.json"
    _, _, gt = synth_impacts.write_session(wav, gt_json, seed=0)

    detected = detect_impacts(wav)
    detected_ms = [d.t_ms for d in detected]

    # nearest detection per planted impact
    matched: list[tuple[int, int, int]] = []
    unmatched: list[int] = []
    for planted in gt.impacts_ms:
        if not detected_ms:
            unmatched.append(planted)
            continue
        nearest = min(detected_ms, key=lambda d: abs(d - planted))
        err = nearest - planted
        if abs(err) <= TIMING_TOLERANCE_MS:
            matched.append((planted, nearest, err))
        else:
            unmatched.append(planted)

    classes = [_classify(d, gt) for d in detected_ms]
    distractor_fps = [d for d, c in zip(detected_ms, classes) if c == "distractor"]
    silent_fps = [d for d, c in zip(detected_ms, classes) if c == "silent"]
    max_timing_err_ms = max((abs(m[2]) for m in matched), default=0)

    diag = (
        f"\nplanted_ms      = {gt.impacts_ms}"
        f"\ndistractors_ms  = {gt.distractors_ms}"
        f"\ndetected_ms     = {detected_ms}"
        f"\nmatched (planted, detected, err_ms) = {matched}"
        f"\nunmatched_planted = {unmatched}"
        f"\ndistractor_fps  = {distractor_fps}"
        f"\nsilent_fps      = {silent_fps}"
        f"\nmax_timing_err  = {max_timing_err_ms} ms"
    )

    # If any of these fail, the message exposes the actual numbers — per
    # plan, we do NOT tune to make the test pass.
    assert not unmatched, f"missed planted impacts (recall < 1.0): {unmatched}{diag}"
    assert not distractor_fps, f"distractor false positives: {distractor_fps}{diag}"
    assert not silent_fps, f"silent-region false positives: {silent_fps}{diag}"
    assert max_timing_err_ms <= TIMING_TOLERANCE_MS, (
        f"max timing error {max_timing_err_ms} ms exceeds tolerance{diag}"
    )


def test_segmenter_detects_impact_just_past_warmup(tmp_path):
    """Regression for the ONSET_WARMUP_MS startup-artifact mask.

    A real impact planted just past the 150 ms warmup window must still be
    detected. If a future change re-introduces the spectral-flux startup
    artifact (or widens the warmup mask past where real impacts can land),
    this test catches it: the artifact would shadow the 200 ms impact via
    find_peaks' amplitude-priority dedup, and the assertion fails.
    """
    wav = tmp_path / "near_start.wav"
    gt_json = tmp_path / "near_start.gt.json"
    synth_impacts.write_session(
        wav,
        gt_json,
        duration_s=2.0,
        impacts_ms=(200,),
        distractors_ms=(),
        seed=0,
    )

    detected = detect_impacts(wav)
    detected_ms = [d.t_ms for d in detected]

    nearest = min(detected_ms, key=lambda d: abs(d - 200), default=None)
    assert nearest is not None and abs(nearest - 200) <= TIMING_TOLERANCE_MS, (
        f"impact at 200 ms not detected within ±{TIMING_TOLERANCE_MS} ms;"
        f" detected={detected_ms}"
    )
