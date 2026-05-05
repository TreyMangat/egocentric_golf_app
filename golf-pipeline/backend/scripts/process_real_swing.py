"""Process a real swing video through the local pipeline end-to-end.

Takes a path to a .mov / .mp4, uploads it to S3 under a fresh session id,
triggers the ProcessSession Temporal workflow, blocks until it finishes,
and prints `/swing/<id>` URLs for every swing the workflow wrote to Mongo.

Usage
-----
    cd golf-pipeline/backend
    python scripts/process_real_swing.py path/to/swing.mp4 --club driver --view DTL

Contract notes
--------------
- The workflow's tag matcher (`temporal/activities.py:_closest_tag`) is
  unbounded nearest-neighbor by `abs(tMs - impact_ms)` — no tolerance,
  no fallback. A single tag event at `tMs=0` carrying --club / --view is
  therefore broadcast to every detected swing. That's what we send.
- Audio is non-negotiable: the segmenter is audio-anchored. We ffprobe
  the input before uploading and refuse to proceed if the file has no
  audio stream — saves a wasted Temporal run on a silent video.

HEVC on Windows
---------------
MediaPipe's video reader (OpenCV under the hood) sometimes can't decode
HEVC `.mov` straight from an iPhone without a system codec pack. Symptom:
pose inference returns 0 frames or fails outright. Workaround — transcode
to H.264 first, copy the audio:

    ffmpeg -i in.mov -c:v libx264 -c:a copy out.mov

Then run this script on `out.mov`. We deliberately don't auto-transcode;
when pose quality suffers on a non-spec input that's a signal we want to
see, not gate against.

Prereqs
-------
Same set as the smoke test (`scripts/test_local_pipeline.py`):
  1. backend/.env populated with AWS, MONGO_URI, USER_ID, LOCAL_DEV=1
  2. temporal server start-dev
  3. python -m golf_pipeline.temporal.worker
  4. uvicorn golf_pipeline.api.server:app --port 8000
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import shutil
import subprocess
import sys
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

from rich.console import Console

# scripts/ isn't on the package path; reuse the smoke test's plumbing as a
# sibling import. Lazy private functions are fine — this is a dev tool.
_SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(_SCRIPTS))
from test_local_pipeline import (  # noqa: E402
    _http_post_json,
    _http_put_file,
    _preflight,
    _register_session,
    _wait_for_workflow,
)

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

console = Console()

VALID_CLUBS = (
    "driver", "3w", "5w", "hybrid",
    "3i", "4i", "5i", "6i", "7i", "8i", "9i",
    "pw", "gw", "sw", "lw", "putter",
)
VALID_VIEWS = ("DTL", "FO")


# ─── ffprobe pre-flight ────────────────────────────────────────────────────────


def _probe(path: Path) -> dict:
    """Probe a media file. Prefers ffprobe; falls back to parsing
    `ffmpeg -i` stderr when ffprobe isn't on PATH (some Windows ffmpeg
    distributions ship without it)."""
    if shutil.which("ffprobe"):
        proc = subprocess.run(
            [
                "ffprobe", "-v", "error",
                "-print_format", "json",
                "-show_format", "-show_streams",
                str(path),
            ],
            capture_output=True,
        )
        if proc.returncode != 0:
            err = proc.stderr.decode("utf-8", errors="replace").strip()
            sys.exit(f"ffprobe failed on {path}: {err}")
        data = json.loads(proc.stdout)
        v = next((s for s in data["streams"] if s["codec_type"] == "video"), None)
        a = next((s for s in data["streams"] if s["codec_type"] == "audio"), None)
        if v is None:
            sys.exit(f"{path}: no video stream found")
        # avg_frame_rate is "num/den" — guard the den=0 case some containers emit.
        num_s, _, den_s = v.get("avg_frame_rate", "0/1").partition("/")
        num = int(num_s) if num_s else 0
        den = int(den_s) if den_s else 1
        fps = num / den if den else 0.0
        return {
            "container": data["format"].get("format_name", "?"),
            "vcodec": v.get("codec_name", "?"),
            "width": int(v.get("width", 0)),
            "height": int(v.get("height", 0)),
            "fps": fps,
            "duration_s": float(data["format"].get("duration", 0) or 0),
            "has_audio": a is not None,
            "acodec": a.get("codec_name") if a else None,
        }

    # Fallback: parse ffmpeg's stderr. ffmpeg exits 1 when given no output
    # file, which is fine — we only want the probe banner.
    proc = subprocess.run(["ffmpeg", "-i", str(path)], capture_output=True)
    err = proc.stderr.decode("utf-8", errors="replace")
    if "Video:" not in err:
        sys.exit(f"{path}: ffmpeg could not find a video stream")

    vmatch = re.search(
        r"Video:\s*(\w+).*?(\d+)x(\d+).*?([\d.]+)\s*fps", err
    )
    cmatch = re.search(r"Input #0,\s*([^,]+),", err)
    dmatch = re.search(r"Duration:\s*(\d+):(\d+):([\d.]+)", err)
    duration = 0.0
    if dmatch:
        h, m, s = dmatch.groups()
        duration = int(h) * 3600 + int(m) * 60 + float(s)
    return {
        "container": cmatch.group(1).strip() if cmatch else "?",
        "vcodec": vmatch.group(1) if vmatch else "?",
        "width": int(vmatch.group(2)) if vmatch else 0,
        "height": int(vmatch.group(3)) if vmatch else 0,
        "fps": float(vmatch.group(4)) if vmatch else 0.0,
        "duration_s": duration,
        "has_audio": "Audio:" in err,
        "acodec": None,
    }


def _print_probe(path: Path, info: dict) -> None:
    audio_tag = info["acodec"] or ("yes" if info["has_audio"] else "no")
    console.print(
        f"[bold]{path.name}[/bold]  "
        f"container={info['container']}  "
        f"vcodec={info['vcodec']}  "
        f"{info['width']}x{info['height']}@{info['fps']:.2f}fps  "
        f"duration={info['duration_s']:.2f}s  "
        f"audio={audio_tag}"
    )


# ─── pipeline drive ────────────────────────────────────────────────────────────


def _content_type_for(path: Path) -> str:
    ext = path.suffix.lower()
    if ext == ".mp4":
        return "video/mp4"
    if ext in (".mov", ".qt"):
        return "video/quicktime"
    return "application/octet-stream"


def _presign_real_upload(
    api: str, user_id: str, session_id: str, content_type: str
) -> tuple[str, str]:
    body = _http_post_json(
        f"{api}/api/v1/upload/presign",
        {
            "user_id": user_id,
            "session_id": session_id,
            "clip_id": "session",
            "content_type": content_type,
        },
    )
    return body["upload_url"], body["s3_key"]


def _finalize_with_tag(
    api: str, user_id: str, session_id: str, club: str, view: str
) -> str:
    """Trigger ProcessSession with a single tag event at tMs=0. The tag
    matcher in `_closest_tag` is unbounded nearest-neighbor, so this one
    event applies to every detected impact in the session."""
    body = _http_post_json(
        f"{api}/api/v1/sessions/{session_id}/finalize",
        {
            "user_id": user_id,
            "capture_metadata": {
                "tagEvents": [{"tMs": 0, "club": club, "view": view}],
                "location": "real-swing",
            },
        },
    )
    return body["workflowId"]


async def _list_session_swings(session_id: str):
    from golf_pipeline.db.client import list_swings_in_session
    return await list_swings_in_session(session_id)


# ─── main ──────────────────────────────────────────────────────────────────────


async def _main_async(args: argparse.Namespace) -> None:
    video_path = Path(args.video).resolve()
    if not video_path.exists():
        sys.exit(f"file not found: {video_path}")

    if shutil.which("ffmpeg") is None:
        sys.exit("ffmpeg not on PATH. Install ffmpeg and retry.")

    info = _probe(video_path)
    _print_probe(video_path, info)
    if not info["has_audio"]:
        sys.exit(
            f"\n{video_path.name} has no audio track. The segmenter is "
            "audio-anchored; uploading would burn a Temporal run on a "
            "guaranteed-zero-impact session. Aborting."
        )

    _preflight(args.api)

    from golf_pipeline.config import get_config
    cfg = get_config()
    user_id = args.user_id or cfg.user_id
    session_id = (
        f"swing_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
    )
    started_at = datetime.now(UTC)
    console.log(f"session_id={session_id} user_id={user_id}")

    _register_session(args.api, user_id, session_id, started_at)
    content_type = _content_type_for(video_path)
    upload_url, s3_key = _presign_real_upload(
        args.api, user_id, session_id, content_type
    )
    console.log(f"s3_key={s3_key}")

    console.log(f"uploading {video_path.name} as {content_type}")
    try:
        _http_put_file(upload_url, video_path, content_type)
    except Exception as e:  # noqa: BLE001 — diagnostic surface, not silenced
        url_prefix = upload_url.split("?", 1)[0]
        console.print(f"[red]upload failed:[/red] {e}")
        console.print(
            f"  bucket = {cfg.aws.bucket}\n"
            f"  key    = {s3_key}\n"
            f"  url    = {url_prefix}?…(signed)"
        )
        sys.exit(1)

    workflow_id = _finalize_with_tag(
        args.api, user_id, session_id, args.club, args.view
    )
    console.log(f"workflowId={workflow_id}")
    console.log("waiting for workflow (segment → cut → pose → metrics → mongo)…")

    t0 = time.monotonic()
    try:
        result = await _wait_for_workflow(workflow_id, timeout_s=args.timeout)
    except TimeoutError:
        console.print(
            f"[red]workflow timeout after {args.timeout:.0f}s[/red]\n"
            f"  workflowId = {workflow_id}\n"
            f"  Temporal UI: http://localhost:8233/namespaces/default/workflows/{workflow_id}"
        )
        sys.exit(1)
    except Exception as e:  # noqa: BLE001
        console.print(
            f"[red]workflow failed:[/red] {e}\n"
            f"  workflowId = {workflow_id}\n"
            f"  Temporal UI: http://localhost:8233/namespaces/default/workflows/{workflow_id}"
        )
        sys.exit(1)

    elapsed = time.monotonic() - t0
    console.log(f"workflow finished in {elapsed:.1f}s")
    console.print_json(json.dumps(result, default=str))

    swings = await _list_session_swings(session_id)
    if not swings:
        console.print(
            f"\n[yellow]workflow completed but no swings landed in Mongo "
            f"for session {session_id}.[/yellow]\n"
            "  Most likely cause: the audio segmenter found no impacts in "
            "this video. Check the worker terminal for `Detected 0 swings` "
            f"or open http://localhost:8233 to inspect workflow {workflow_id}."
        )
        sys.exit(2)

    console.print(
        f"\n[bold green]✔ {len(swings)} swing(s) written to Mongo[/bold green]"
    )
    for s in swings:
        console.print(f"  {s.id}  →  http://localhost:3000/swing/{s.id}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("video", help="Path to a .mov or .mp4 swing/session video")
    parser.add_argument(
        "--club", required=True, choices=VALID_CLUBS,
        help="Club used for every swing in this video (broadcast to all detected impacts)",
    )
    parser.add_argument(
        "--view", required=True, choices=VALID_VIEWS,
        help="DTL (down-the-line) or FO (face-on)",
    )
    parser.add_argument("--api", default="http://localhost:8000")
    parser.add_argument(
        "--user-id", default=None,
        help="Override user_id (defaults to USER_ID from .env)",
    )
    parser.add_argument(
        "--timeout", type=float, default=1800.0,
        help="Seconds to wait for the workflow before giving up (default 1800)",
    )
    args = parser.parse_args()
    asyncio.run(_main_async(args))


if __name__ == "__main__":
    main()
