"""End-to-end local runner — useful for debugging the pipeline without
spinning up Temporal or Modal.

Usage:
    python scripts/run_local.py --video sample.mov --club 7i --view DTL [--lead L]

Writes:
    artifacts/{stem}.json   (metrics + phases)
    artifacts/{stem}.npz    (keypoints)
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Annotated

import numpy as np
import typer
from rich.console import Console
from rich.table import Table

from golf_pipeline.metrics.compute import compute_all
from golf_pipeline.modal_pose.inference import extract_pose_local
from golf_pipeline.segmentation.audio_impact import detect_impacts, extract_audio

app = typer.Typer(add_completion=False)
console = Console()


@app.command()
def main(
    video: Annotated[
        Path,
        typer.Option(..., exists=True, dir_okay=False, readable=True),
    ],
    club: str = "7i",
    view: Annotated[str, typer.Option(help="DTL or FO")] = "DTL",
    lead: Annotated[str, typer.Option(help="Lead side: L (RH golfer) or R (LH golfer)")] = "L",
    out_dir: Path = Path("artifacts"),
):
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = video.stem

    with tempfile.TemporaryDirectory() as td:
        wav = Path(td) / "audio.wav"
        console.log("Extracting audio…")
        extract_audio(video, wav)

        console.log("Detecting impacts…")
        impacts = detect_impacts(wav)
        console.log(f"  found {len(impacts)} impact(s)")

    console.log("Running pose inference (CPU local)…")
    npz_path = out_dir / f"{stem}.npz"
    pose_info = extract_pose_local(str(video), str(npz_path))
    console.log(f"  {pose_info['frames']} frames @ {pose_info['fps']:.1f} fps")

    kp = np.load(npz_path)["keypoints_world"]
    fps = pose_info["fps"]

    impact_frame = None
    if impacts:
        # if we found impacts, anchor on the strongest one
        best = max(impacts, key=lambda i: i.confidence)
        impact_frame = int(round(best.t_ms / 1000 * fps))

    phases, metrics, ranges = compute_all(kp, fps=fps, lead_side=lead, impact_frame=impact_frame)

    summary = {
        "video": str(video),
        "club": club,
        "view": view,
        "lead": lead,
        "fps": fps,
        "frames": int(kp.shape[0]),
        "impactDetections": len(impacts),
        "phases": phases.model_dump(by_alias=True),
        "metrics": metrics.model_dump(by_alias=True),
        "ranges": {k: v.model_dump() for k, v in ranges.items()},
    }
    out_json = out_dir / f"{stem}.json"
    out_json.write_text(json.dumps(summary, indent=2, default=str))

    _print_metrics_table(metrics, ranges)
    console.log(f"\nWrote {out_json} and {npz_path}")


def _print_metrics_table(metrics, ranges):
    t = Table(title="Tier 1 metrics")
    t.add_column("metric")
    t.add_column("value", justify="right")
    t.add_column("target", justify="right")
    t.add_column("status")

    m = metrics.model_dump(by_alias=True)
    for key, val in m.items():
        rng = ranges.get(key)
        target = f"{rng.target[0]:.0f}–{rng.target[1]:.0f}" if rng else "—"
        status = rng.status if rng else "—"
        t.add_row(key, str(val), target, status)
    console.print(t)


if __name__ == "__main__":
    try:
        app()
    except subprocess.CalledProcessError as e:
        console.print(f"[red]ffmpeg failed:[/red] {e}")
        sys.exit(1)
