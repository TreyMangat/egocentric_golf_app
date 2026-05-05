"""Diagnose Tier 1 metrics for a real swing — read-only.

Loads a swing document from Mongo, downloads its keypoints `.npz` from S3,
and emits a Markdown report comparing the metric values stored on the
swing (computed by whatever code shipped at processing time) to the values
the current code would produce given the same keypoints. The verdict at
the top of the report is one of:
    "formulas" / "phase detection" / "pose quality" / "mixed" / "ok"

Usage
-----
    cd golf-pipeline/backend
    python scripts/diagnose_swing_metrics.py <swing_id>
        [--out ../docs/diagnose_<swing_id>.md]

No Mongo writes. Imports `compute_all` for the recompute pass.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import tempfile
from pathlib import Path

import numpy as np

from golf_pipeline.db.client import get_swing
from golf_pipeline.metrics.compute import compute_all
from golf_pipeline.schemas import Phases, Swing
from golf_pipeline.storage.s3 import download_to_path, parse_s3_uri

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# joint indices (mirror metrics/compute.py)
NOSE = 0
LSH, RSH = 11, 12
LEL, REL = 13, 14
LWR, RWR = 15, 16
LHIP, RHIP = 23, 24

JOINT_NAMES: dict[int, str] = {
    NOSE: "nose",
    LSH: "L_shoulder", RSH: "R_shoulder",
    LEL: "L_elbow",    REL: "R_elbow",
    LWR: "L_wrist",    RWR: "R_wrist",
    LHIP: "L_hip",     RHIP: "R_hip",
}
KEY_JOINTS = [NOSE, LSH, RSH, LHIP, RHIP, LWR, RWR]


# ─── helpers ──────────────────────────────────────────────────────────────────


def _fmt_xyz(p: np.ndarray) -> str:
    if p.size < 4 or not np.all(np.isfinite(p[:3])):
        return "       nan        nan        nan   (vis=  nan)"
    return (
        f"x={p[0]:+8.3f}  y={p[1]:+8.3f}  z={p[2]:+8.3f}   "
        f"(vis={p[3]:5.2f})"
    )


def _angle_to_vertical_yup(v: np.ndarray) -> float:
    """Unsigned angle (deg) from +Y after a y-axis flip — i.e., the same
    transformation `compute_all` applies internally. Used to surface what
    the spine-tilt formula sees post-fix."""
    flipped = np.array([v[0], -v[1], v[2]])
    n = float(np.linalg.norm(flipped)) + 1e-9
    cos = float(np.clip(flipped[1] / n, -1.0, 1.0))
    return float(np.degrees(np.arccos(cos)))


def _phase_dump(phases: Phases | None, fps: float) -> dict[str, tuple[int, int]]:
    if phases is None:
        return {}
    return {
        "address":    (phases.address.frame,    int(phases.address.frame    / fps * 1000)),
        "takeaway":   (phases.takeaway.frame,   int(phases.takeaway.frame   / fps * 1000)),
        "top":        (phases.top.frame,        int(phases.top.frame        / fps * 1000)),
        "transition": (phases.transition.frame, int(phases.transition.frame / fps * 1000)),
        "impact":     (phases.impact.frame,     int(phases.impact.frame     / fps * 1000)),
        "finish":     (phases.finish.frame,     int(phases.finish.frame     / fps * 1000)),
    }


# ─── data fetch ───────────────────────────────────────────────────────────────


async def _fetch_swing(swing_id: str) -> Swing:
    swing = await get_swing(swing_id)
    if swing is None:
        sys.exit(f"swing not found in Mongo: {swing_id!r}")
    if swing.keypoints is None or not swing.keypoints.storage_ref:
        sys.exit(f"swing {swing_id} has no keypoints.storageRef")
    return swing


def _load_kp(storage_ref: str) -> tuple[np.ndarray, np.ndarray, float]:
    _, key = parse_s3_uri(storage_ref)
    with tempfile.TemporaryDirectory() as td:
        local = Path(td) / "kp.npz"
        download_to_path(key, str(local))
        # Eagerly materialize and close before leaving the context — on
        # Windows np.load keeps a handle to the file otherwise.
        with np.load(local) as npz:
            kp_world = np.array(npz["keypoints_world"])
            kp_image = np.array(npz["keypoints_image"])
            fps = float(npz["fps"])
    return kp_world, kp_image, fps


# ─── report builder ───────────────────────────────────────────────────────────


def _build_report(
    swing: Swing,
    kp_world: np.ndarray,
    kp_image: np.ndarray,
    fps: float,
) -> str:
    n = kp_world.shape[0]

    # The audio-anchored impact frame is what the activity passes into
    # compute_all. We can recover it from either stored phases (if present)
    # or fall back to half-clip if the stored doc was rejected.
    if swing.phases is not None:
        stored_impact_f = swing.phases.impact.frame
    else:
        stored_impact_f = n // 2

    # Re-run the metrics pipeline at the current code revision. compute_all
    # internally flips +Y down → +Y up for its helpers; we hand it the raw
    # BlazePose array.
    new_phases, new_metrics, new_ranges = compute_all(
        kp_world,
        fps=fps,
        lead_side="L",
        impact_frame=stored_impact_f,
    )

    # ─── pose-quality stats (raw, pre-flip) ──────────────────
    valid_mask = np.isfinite(kp_world[..., :3]).all(axis=-1)
    full_frames = int(valid_mask.all(axis=1).sum())
    empty_frames = int((~valid_mask.any(axis=1)).sum())
    vis = kp_world[..., 3]
    finite_vis = vis[np.isfinite(vis)]
    mean_vis = float(np.mean(finite_vis)) if finite_vis.size else float("nan")

    # ─── y-axis convention sanity check ──────────────────────
    addr_for_axis = (
        new_phases.address.frame if new_phases else 0
    )
    sh_y_addr = float((kp_world[addr_for_axis, LSH, 1] + kp_world[addr_for_axis, RSH, 1]) / 2)
    hip_y_addr = float((kp_world[addr_for_axis, LHIP, 1] + kp_world[addr_for_axis, RHIP, 1]) / 2)
    head_y_addr = float(kp_world[addr_for_axis, NOSE, 1])
    y_is_up_in_storage = sh_y_addr > hip_y_addr

    # ─── spine-tilt detail (recomputed) ──────────────────────
    addr_f = new_phases.address.frame
    pelvis_addr = (kp_world[addr_f, LHIP, :3] + kp_world[addr_f, RHIP, :3]) / 2
    shoulders_addr = (kp_world[addr_f, LSH, :3] + kp_world[addr_f, RSH, :3]) / 2
    spine_v = shoulders_addr - pelvis_addr
    spine_norm = float(np.linalg.norm(spine_v))
    spine_yup_angle = _angle_to_vertical_yup(spine_v)

    # ─── head-sway frame-by-frame (recomputed window) ────────
    head_addr_world = kp_world[addr_f, NOSE]
    head_track = []
    impact_recomputed = new_phases.impact.frame
    finish_recomputed = new_phases.finish.frame
    for f in range(addr_f, impact_recomputed + 1):
        d = kp_world[f, NOSE] - head_addr_world
        head_track.append((f, d))

    # ─── verdict ─────────────────────────────────────────────
    suspects: list[str] = []

    # phase detection: top → impact in plausible range?
    new_down_ms = int((new_phases.impact.frame - new_phases.top.frame) / fps * 1000)
    if new_down_ms < 80 or (new_phases.impact.frame - new_phases.top.frame) <= 2:
        suspects.append("phase detection")

    # formulas: spine tilt should never read > 90° under the +Y-up
    # interpretation (forward-bend < 90° is always right for a real
    # swing). The recomputed value is the post-load-flip value.
    new_spine_addr = new_metrics.spine_tilt_at_address_deg
    if new_spine_addr is not None and new_spine_addr > 90.0:
        suspects.append("formulas")

    # pose quality
    win_valid = valid_mask[addr_f : finish_recomputed + 1, KEY_JOINTS]
    bad_cells = int((~win_valid).sum())
    total_cells = int(win_valid.size)
    bad_frac = bad_cells / total_cells if total_cells else 0.0
    if bad_frac > 0.20 or mean_vis < 0.5:
        suspects.append("pose quality")

    # de-dup
    seen: set[str] = set()
    suspects = [s for s in suspects if not (s in seen or seen.add(s))]

    if not suspects:
        verdict = "ok"
    elif len(suspects) == 1:
        verdict = suspects[0]
    else:
        verdict = "mixed"

    # ─── write report ────────────────────────────────────────
    lines: list[str] = []
    out = lines.append

    out(f"# Diagnose `{swing.id}`")
    out("")
    out(f"> **Verdict (post-fix recompute):** `{verdict}`  &nbsp; "
        f"(suspects: {', '.join(suspects) or 'none'})")
    out("")
    out("Stored metrics on the swing doc were produced by the pipeline "
        "version that processed the upload. The 'recomputed' columns below "
        "are produced by the *current* code path on the same `.npz`. A "
        "side-by-side mismatch is expected when the fix changes the answer; "
        "an unchanged value confirms the fix didn't regress that metric.")
    out("")

    # ─── facts ───────────────────────────────────────────────
    out("## Swing facts")
    out(f"- status: `{swing.status}`")
    out(f"- motion score: `{swing.motion_score:.3f}` m/s peak")
    out(f"- club: `{swing.capture.club.value}`  |  view: `{swing.capture.view.value}`")
    out(f"- fps: capture says `{swing.capture.fps}`, .npz says `{fps:.2f}`")
    out(f"- frames: `{n}`")
    out(f"- pipeline version (stored): "
        f"`{swing.pipeline.version if swing.pipeline else 'unknown'}`")
    out(f"- storageRef: `{swing.keypoints.storage_ref}`")
    out("")

    # ─── y-axis convention ───────────────────────────────────
    out("## World-space axis convention (raw `.npz`)")
    out(f"- shoulder-mid y at address: `{sh_y_addr:+.3f}`")
    out(f"- hip-mid y at address:       `{hip_y_addr:+.3f}`")
    out(f"- nose y at address:          `{head_y_addr:+.3f}`")
    out(f"- inferred convention in storage: **+Y is "
        f"{'UP' if y_is_up_in_storage else 'DOWN'}**.")
    out("")
    out("BlazePose ships +Y down. `compute_all` flips at its load boundary "
        "so internal helpers see +Y up. This section reads the raw `.npz` "
        "without flipping — it should always say DOWN for real swings.")
    out("")

    # ─── phases side-by-side ─────────────────────────────────
    out("## Phases — stored vs recomputed")
    out("")
    stored_phases = _phase_dump(swing.phases, fps)
    new_phase_dump = _phase_dump(new_phases, fps)
    out("| phase | stored frame | stored tMs | recomputed frame | recomputed tMs |")
    out("|---|---:|---:|---:|---:|")
    for name in ("address", "takeaway", "top", "transition", "impact", "finish"):
        s_f, s_t = stored_phases.get(name, (None, None))
        r_f, r_t = new_phase_dump.get(name, (None, None))
        out(f"| {name} | "
            f"{s_f if s_f is not None else '—'} | "
            f"{s_t if s_t is not None else '—'} | "
            f"{r_f if r_f is not None else '—'} | "
            f"{r_t if r_t is not None else '—'} |")
    out("")

    # ─── metrics side-by-side ────────────────────────────────
    out("## Metrics — stored vs recomputed")
    out("")
    stored_m = swing.metrics.model_dump(by_alias=True)
    new_m = new_metrics.model_dump(by_alias=True)
    metric_keys = [
        "tempoRatioBackswingDownswing",
        "backswingDurationMs",
        "downswingDurationMs",
        "shoulderTurnAtTopDeg",
        "hipTurnAtTopDeg",
        "xFactorDeg",
        "wristHingeMaxDeg",
        "headSwayMaxMm",
        "headLiftMaxMm",
        "spineTiltAtAddressDeg",
        "spineTiltAtImpactDeg",
        "leadArmAngleAtTopDeg",
    ]
    out("| metric | stored | recomputed | target |")
    out("|---|---:|---:|---:|")
    for k in metric_keys:
        sv = stored_m.get(k)
        rv = new_m.get(k)
        rng = new_ranges.get(k)
        target = (
            f"{rng.target[0]:g}–{rng.target[1]:g}"
            if rng is not None else "—"
        )
        out(f"| `{k}` | "
            f"{sv if sv is not None else '—'} | "
            f"{rv if rv is not None else '—'} | "
            f"{target} |")
    out("")

    # ─── pose quality ────────────────────────────────────────
    out("## Pose quality (raw `.npz`)")
    out(f"- frames with all 33 joints valid: `{full_frames}/{n}`")
    out(f"- frames with zero valid joints: `{empty_frames}/{n}`")
    out(f"- mean visibility: `{mean_vis:.3f}`")
    out(f"- inside [recomputed address, recomputed finish], invalid cells "
        f"across key joints: `{bad_cells}/{total_cells}` "
        f"({100*bad_frac:.1f}%)")
    out("")

    # ─── joint positions at recomputed phase frames ──────────
    out("## World-space joint positions at recomputed phases")
    out("")
    out("`vis` is the BlazePose visibility score (≈ confidence ∈ [0,1]). "
        "Values shown are raw (+Y down) — the load-flip is only applied "
        "inside `compute_all`.")
    out("")
    for label, frame in [
        ("Address", new_phases.address.frame),
        ("Top",     new_phases.top.frame),
        ("Impact",  new_phases.impact.frame),
        ("Finish",  new_phases.finish.frame),
    ]:
        out(f"### {label} (frame {frame})")
        out("```")
        for j in KEY_JOINTS:
            out(f"{JOINT_NAMES[j]:<12} {_fmt_xyz(kp_world[frame, j])}")
        out("```")
        out("")

    # ─── spine-tilt detail ───────────────────────────────────
    out("## Spine tilt at recomputed address (detail)")
    out(
        f"- pelvis_mid (raw):   x={pelvis_addr[0]:+.3f}  "
        f"y={pelvis_addr[1]:+.3f}  z={pelvis_addr[2]:+.3f}"
    )
    out(
        f"- shoulder_mid (raw): x={shoulders_addr[0]:+.3f}  "
        f"y={shoulders_addr[1]:+.3f}  z={shoulders_addr[2]:+.3f}"
    )
    out(f"- |spine|: `{spine_norm:.3f} m`")
    out(f"- angle of (sh_mid − pelvis_mid) vs +Y after the load-flip: `{spine_yup_angle:.1f}°`")
    out(f"- recomputed `spineTiltAtAddressDeg`: `{new_metrics.spine_tilt_at_address_deg}°`")
    out(f"- stored `spineTiltAtAddressDeg`: `{swing.metrics.spine_tilt_at_address_deg}°`")
    out("")

    # ─── head sway frame-by-frame (using recomputed phases) ──
    out("## Head sway: nose displacement, recomputed address → recomputed impact")
    out("Raw `.npz` displacement from address-frame nose. ×1000 → mm.")
    out("")
    out("```")
    out(f"{'frame':>5}  {'tMs':>5}  "
        f"{'Δx_mm':>9}  {'Δy_mm':>9}  {'Δz_mm':>9}  "
        f"{'|Δ|_mm':>9}  {'vis':>4}")
    for f, d in head_track:
        if not np.all(np.isfinite(d[:3])):
            out(f"{f:>5}  {f/fps*1000:>5.0f}  "
                f"{'nan':>9}  {'nan':>9}  {'nan':>9}  "
                f"{'nan':>9}  {'nan':>4}")
            continue
        mag = float(np.linalg.norm(d[:3])) * 1000
        out(f"{f:>5}  {f/fps*1000:>5.0f}  "
            f"{d[0]*1000:>9.1f}  {d[1]*1000:>9.1f}  {d[2]*1000:>9.1f}  "
            f"{mag:>9.1f}  {kp_world[f, NOSE, 3]:>4.2f}")
    out("```")
    out(f"- recomputed `headSwayMaxMm`: `{new_metrics.head_sway_max_mm}`")
    out(f"- stored `headSwayMaxMm`: `{swing.metrics.head_sway_max_mm}`")
    out("")

    # ─── stored ranges (audit) ───────────────────────────────
    out("## Recomputed ranges (status per metric)")
    out("```json")
    out(json.dumps(
        {k: rs.model_dump(by_alias=True) for k, rs in new_ranges.items()},
        indent=2,
    ))
    out("```")
    out("")

    return "\n".join(lines)


# ─── main ─────────────────────────────────────────────────────────────────────


async def _main_async(args: argparse.Namespace) -> None:
    swing = await _fetch_swing(args.swing_id)
    print(f"loaded swing {swing.id}, downloading keypoints…", flush=True)
    kp_world, kp_image, fps = _load_kp(swing.keypoints.storage_ref)
    print(
        f"keypoints: world={kp_world.shape}, image={kp_image.shape}, "
        f"fps={fps:.2f}",
        flush=True,
    )

    report = _build_report(swing, kp_world, kp_image, fps)

    out_path = Path(args.out) if args.out else (
        Path(__file__).resolve().parent.parent.parent
        / "docs" / f"diagnose_{swing.id}.md"
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(report, encoding="utf-8")
    print(f"\nwrote {out_path}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("swing_id", help="Mongo _id of the swing to diagnose")
    parser.add_argument(
        "--out", default=None,
        help="Output path. Defaults to ../docs/diagnose_<swing_id>.md "
             "relative to backend/.",
    )
    args = parser.parse_args()
    asyncio.run(_main_async(args))


if __name__ == "__main__":
    main()
