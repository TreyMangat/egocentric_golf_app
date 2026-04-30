"""End-to-end smoke test for the local pipeline.

What this proves
----------------
The whole backend plumbing works on a laptop without Modal:

    presign upload  ->  S3
                    ->  FastAPI /finalize
                    ->  Temporal ProcessSession workflow
                    ->  audio segmenter (one swing per planted impact)
                    ->  cut_clip activity (ffmpeg)
                    ->  run_pose_inference activity (LOCAL_DEV path: CPU
                        MediaPipe -> upload .npz)
                    ->  compute_metrics_and_write
                    ->  Mongo write
                    ->  Mongo read via list_swings_in_session

What this does NOT prove
------------------------
That the metrics are correct. The synthesized "video" is a black frame --
MediaPipe finds no body, every per-frame keypoint is NaN, the metrics
that come out are mostly NaN/0. We're testing wiring, not analytics.

Prereqs (you start these in separate terminals before running this)
-------------------------------------------------------------------
  1. ffmpeg on PATH
  2. backend/.env populated with real S3_BUCKET, AWS creds, MONGO_URI
     (Atlas free tier is fine), and LOCAL_DEV=1
  3. temporal server start-dev
  4. python -m golf_pipeline.temporal.worker
  5. uvicorn golf_pipeline.api.server:app --reload --port 8000

Usage
-----
  cd backend
  python scripts/test_local_pipeline.py [--api http://localhost:8000]
                                        [--duration 15]
                                        [--timeout 1800]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import uuid
from datetime import UTC, datetime
from pathlib import Path

from rich.console import Console

console = Console()

# scripts/ isn't on the package path; import the synth tool as a sibling.
_SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(_SCRIPTS))


# ─── http (stdlib so we don't lean on a transitive `requests`) ─────────────────


def _http_get_json(url: str, timeout: float = 5.0) -> dict:
    with urllib.request.urlopen(url, timeout=timeout) as r:  # noqa: S310
        return json.loads(r.read().decode("utf-8"))


def _http_post_json(url: str, body: dict, timeout: float = 30.0) -> dict:
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url, data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310
        return json.loads(r.read().decode("utf-8"))


def _http_put_file(
    url: str, file_path: Path, content_type: str, timeout: float = 300.0
) -> None:
    with open(file_path, "rb") as f:
        body = f.read()
    req = urllib.request.Request(
        url, data=body,
        headers={"Content-Type": content_type, "Content-Length": str(len(body))},
        method="PUT",
    )
    with urllib.request.urlopen(req, timeout=timeout):  # noqa: S310
        pass


# ─── preflight ─────────────────────────────────────────────────────────────────


def _preflight(api_base: str) -> None:
    if shutil.which("ffmpeg") is None:
        sys.exit("ffmpeg not on PATH. Install ffmpeg and try again.")

    try:
        from golf_pipeline.config import get_config
        cfg = get_config()
    except Exception as e:  # noqa: BLE001
        sys.exit(
            f"could not load backend/.env config: {e}\n"
            "make sure you're running from the backend directory and that "
            ".env defines S3_BUCKET, MONGO_URI."
        )

    if not cfg.local_dev:
        sys.exit(
            "LOCAL_DEV is disabled in your config. This script needs LOCAL_DEV=1 "
            "in .env so pose runs locally instead of via Modal."
        )

    try:
        ok = _http_get_json(f"{api_base}/healthz")
    except urllib.error.URLError as e:
        sys.exit(
            f"FastAPI /healthz unreachable at {api_base}: {e}\n"
            "Is `uvicorn golf_pipeline.api.server:app --port 8000` running?"
        )
    if not ok.get("ok"):
        sys.exit(f"FastAPI healthz returned non-ok: {ok}")


# ─── synthesize fake session ───────────────────────────────────────────────────


def _make_fake_session_video(
    out_mov: Path, duration_s: float, impacts_ms: tuple[int, ...]
):
    """Mux a black H.264 video with a synthesized impact wav into an .mov.
    Returns the GroundTruth dataclass from synth_impacts.write_session.

    Keyframe interval is set to 1s so cut_clip's `-c copy` cuts land
    cleanly enough to produce playable swing clips.
    """
    import synth_impacts  # noqa: PLC0415

    with tempfile.TemporaryDirectory() as td:
        wav = Path(td) / "audio.wav"
        gt_json = Path(td) / "audio.gt.json"
        _, _, gt = synth_impacts.write_session(
            wav,
            gt_json,
            duration_s=duration_s,
            impacts_ms=impacts_ms,
            distractors_ms=(),  # smoke test: no distractors needed
            seed=0,
        )

        out_mov.parent.mkdir(parents=True, exist_ok=True)
        cmd = [
            "ffmpeg", "-y",
            "-f", "lavfi",
            "-i", f"color=c=black:s=640x480:r=30:d={duration_s}",
            "-i", str(wav),
            "-c:v", "libx264",
            "-pix_fmt", "yuv420p",
            "-g", "30",
            "-c:a", "aac",
            "-shortest",
            str(out_mov),
        ]
        proc = subprocess.run(cmd, capture_output=True)
        if proc.returncode != 0:
            console.print(proc.stderr.decode("utf-8", errors="replace"))
            sys.exit("ffmpeg mux failed; see stderr above")

    return gt


# ─── pipeline drive ────────────────────────────────────────────────────────────


def _register_session(
    api: str, user_id: str, session_id: str, started_at: datetime
) -> None:
    _http_post_json(
        f"{api}/api/v1/sessions",
        {
            "user_id": user_id,
            "session_id": session_id,
            "started_at": started_at.isoformat(),
            "location": "smoke-test",
        },
    )


def _presign(api: str, user_id: str, session_id: str) -> tuple[str, str]:
    body = _http_post_json(
        f"{api}/api/v1/upload/presign",
        {
            "user_id": user_id,
            "session_id": session_id,
            "clip_id": "session",
            "content_type": "video/quicktime",
        },
    )
    return body["upload_url"], body["s3_key"]


def _finalize(api: str, user_id: str, session_id: str, gt) -> str:
    body = _http_post_json(
        f"{api}/api/v1/sessions/{session_id}/finalize",
        {
            "user_id": user_id,
            "capture_metadata": {
                "tagEvents": [
                    {"tMs": int(t), "club": "7i", "view": "DTL"}
                    for t in gt.impacts_ms
                ],
                "location": "smoke-test",
            },
        },
    )
    return body["workflowId"]


async def _wait_for_workflow(workflow_id: str, timeout_s: float) -> dict:
    from temporalio.client import Client

    from golf_pipeline.config import get_config

    cfg = get_config()
    client = await Client.connect(
        cfg.temporal.target, namespace=cfg.temporal.namespace
    )
    handle = client.get_workflow_handle(workflow_id)
    return await asyncio.wait_for(handle.result(), timeout=timeout_s)


async def _print_swings(session_id: str) -> None:
    from golf_pipeline.db.client import list_swings_in_session

    swings = await list_swings_in_session(session_id)
    console.print(
        f"\n[bold]Mongo swings in session {session_id}: "
        f"{len(swings)}[/bold]"
    )
    for s in swings:
        doc = s.model_dump(by_alias=True)
        console.print_json(json.dumps(doc, default=str))


# ─── main ──────────────────────────────────────────────────────────────────────


async def _main_async(args: argparse.Namespace) -> None:
    user_id = "smoke"
    session_id = (
        f"smoke_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
    )
    started_at = datetime.now(UTC)

    _preflight(args.api)
    console.log(f"preflight ok; session_id={session_id}")

    impacts_ms = tuple(args.impacts)

    with tempfile.TemporaryDirectory() as td:
        out_mov = Path(td) / "session.mov"
        console.log(
            f"synthesizing fake session: duration={args.duration}s "
            f"impacts_ms={impacts_ms}"
        )
        gt = _make_fake_session_video(out_mov, args.duration, impacts_ms)

        console.log("registering session via FastAPI")
        _register_session(args.api, user_id, session_id, started_at)

        console.log("requesting presigned upload URL")
        url, s3_key = _presign(args.api, user_id, session_id)
        console.log(f"  s3_key={s3_key}")

        console.log("uploading session.mov to S3")
        _http_put_file(url, out_mov, "video/quicktime")

    console.log("triggering ProcessSession workflow")
    workflow_id = _finalize(args.api, user_id, session_id, gt)
    console.log(f"  workflowId={workflow_id}")

    console.log(
        "waiting for workflow completion (segmenter + per-swing CPU pose)"
    )
    t0 = time.monotonic()
    try:
        result = await _wait_for_workflow(workflow_id, timeout_s=args.timeout)
    except TimeoutError:
        sys.exit(f"workflow {workflow_id} did not finish in {args.timeout}s")
    elapsed = time.monotonic() - t0
    console.log(f"  workflow finished in {elapsed:.1f}s")
    console.print_json(json.dumps(result, default=str))

    await _print_swings(session_id)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api", default="http://localhost:8000")
    parser.add_argument(
        "--duration", type=float, default=15.0,
        help="length of synthesized session video in seconds (default 15)",
    )
    parser.add_argument(
        "--impacts", type=int, nargs="+", default=[5000, 11000],
        help="planted impact times (ms). Each becomes one swing in the pipeline.",
    )
    parser.add_argument(
        "--timeout", type=float, default=1800.0,
        help="seconds to wait for the workflow before giving up",
    )
    args = parser.parse_args()
    asyncio.run(_main_async(args))


if __name__ == "__main__":
    main()
