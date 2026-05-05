"""Re-run Tier 1 metrics on existing swing docs without redoing pose.

Use case: a code fix changes the metrics math (e.g., the y-axis flip + top
clamp from commit 249b91d). Stored swing docs hold the broken values. We
already have the keypoints `.npz` on S3, so there's no reason to re-run
pose inference; just download the keypoints, run the current `compute_all`,
and `$set` the new `phases`, `metrics`, `ranges`, and `pipeline.version`.

Usage
-----
    cd golf-pipeline/backend
    python scripts/backfill_swing_metrics.py <swing_id> [<swing_id> ...]
        [--dry-run]

Skips swings that are rejected, missing keypoints, or have no audio-anchored
impact (we recover impact from the stored `phases.impact.frame` since that
was set from the audio segmenter at processing time). Prints a stored-vs-new
diff per swing. Writes only on swings whose metrics actually changed and
only when not in --dry-run.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import tempfile
from datetime import datetime
from pathlib import Path

import numpy as np

from golf_pipeline.config import get_config
from golf_pipeline.db.client import db, get_swing
from golf_pipeline.metrics.compute import compute_all
from golf_pipeline.schemas import Metrics, Phases, Swing
from golf_pipeline.storage.s3 import download_to_path, parse_s3_uri

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")


def _load_kp(storage_ref: str) -> tuple[np.ndarray, float]:
    _, key = parse_s3_uri(storage_ref)
    with tempfile.TemporaryDirectory() as td:
        local = Path(td) / "kp.npz"
        download_to_path(key, str(local))
        # On Windows np.load keeps a handle until close; eagerly materialize.
        with np.load(local) as npz:
            kp_world = np.array(npz["keypoints_world"])
            fps = float(npz["fps"])
    return kp_world, fps


def build_update_doc(
    swing: Swing,
    kp_world: np.ndarray,
    fps: float,
    pipeline_version: str,
) -> dict:
    """Run the current `compute_all` against the swing's keypoints and
    return a Mongo `$set` document for the metric-related fields.

    Pure (no I/O) — the script and its tests both call this.
    """
    if swing.phases is None:
        raise ValueError(
            f"swing {swing.id} has no stored phases (likely rejected by the "
            "motion gate); nothing to backfill."
        )
    impact_frame = swing.phases.impact.frame

    phases, metrics, ranges = compute_all(
        kp_world,
        fps=fps,
        lead_side="L",
        impact_frame=impact_frame,
    )

    update = {
        "phases": phases.model_dump(by_alias=True),
        "metrics": metrics.model_dump(by_alias=True),
        "ranges": {k: rs.model_dump(by_alias=True) for k, rs in ranges.items()},
        "pipeline.version": pipeline_version,
        "pipeline.backfilledAt": datetime.utcnow(),
    }
    return update


def _phases_changed(stored: Phases | None, new_metrics_doc: dict) -> bool:
    if stored is None:
        return True
    new_phases = new_metrics_doc["phases"]
    for key in ("address", "takeaway", "top", "transition", "impact", "finish"):
        if getattr(stored, key).frame != new_phases[key]["frame"]:
            return True
    return False


def _metrics_changed(stored: Metrics, new_metrics_doc: dict) -> bool:
    stored_dump = stored.model_dump(by_alias=True)
    return stored_dump != new_metrics_doc["metrics"]


def _print_diff(swing: Swing, update: dict) -> None:
    sm = swing.metrics.model_dump(by_alias=True)
    nm = update["metrics"]
    print(f"\n  swing {swing.id}  ({swing.capture.club.value})")
    stored_v = swing.pipeline.version if swing.pipeline else "?"
    print(f"    pipeline.version: {stored_v} → {update['pipeline.version']}")
    if swing.phases is not None:
        sp = swing.phases
        np_ = update["phases"]
        for k in ("address", "takeaway", "top", "transition", "impact", "finish"):
            sf = getattr(sp, k).frame
            nf = np_[k]["frame"]
            marker = " " if sf == nf else "*"
            print(f"    {marker} phase.{k:<10} {sf:>4} → {nf:<4}")
    for k in sorted(set(sm) | set(nm)):
        sv, nv = sm.get(k), nm.get(k)
        marker = " " if sv == nv else "*"
        print(f"    {marker} {k:<32} {str(sv):>10} → {nv}")


async def _process_one(swing_id: str, dry_run: bool) -> bool:
    swing = await get_swing(swing_id)
    if swing is None:
        print(f"  skip {swing_id}: not found in Mongo")
        return False
    if swing.status != "accepted":
        print(f"  skip {swing_id}: status={swing.status!r}")
        return False
    if swing.keypoints is None or not swing.keypoints.storage_ref:
        print(f"  skip {swing_id}: no keypoints.storageRef")
        return False
    if swing.phases is None:
        print(f"  skip {swing_id}: stored phases are missing")
        return False

    print(f"  backfill {swing_id}: downloading keypoints…")
    kp_world, fps = _load_kp(swing.keypoints.storage_ref)
    cfg = get_config()
    update = build_update_doc(swing, kp_world, fps, cfg.pipeline_version)
    _print_diff(swing, update)

    if not (_phases_changed(swing.phases, update) or _metrics_changed(swing.metrics, update)):
        print("    no change — leaving Mongo alone")
        return False

    if dry_run:
        print("    --dry-run: would $set the diff above")
        return True

    result = await db().swings.update_one({"_id": swing.id}, {"$set": update})
    if result.matched_count != 1:
        print(f"    [WARN] update matched {result.matched_count} documents")
        return False
    print("    ✔ updated")
    return True


async def _main_async(args: argparse.Namespace) -> None:
    print(f"backfilling {len(args.swing_ids)} swing(s) "
          f"(dry_run={args.dry_run})")
    n_changed = 0
    for swing_id in args.swing_ids:
        if await _process_one(swing_id, args.dry_run):
            n_changed += 1
    suffix = " (dry run — no writes)" if args.dry_run else ""
    print(f"\n{n_changed}/{len(args.swing_ids)} swing(s) "
          f"would be / were updated{suffix}.")


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("swing_ids", nargs="+", help="Mongo _ids to backfill")
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Print the diff but skip the Mongo write.",
    )
    args = parser.parse_args()
    asyncio.run(_main_async(args))


if __name__ == "__main__":
    main()
