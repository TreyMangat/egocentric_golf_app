"""Upload an external swing video through the local pipeline.

This is the PWA upload path without the browser: register a session, presign a
PUT, upload the local video to S3, finalize the session, wait for Temporal, and
print dashboard links for any Mongo swings.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from rich.console import Console

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
VALID_OUTCOMES = ("good", "ok", "bad")


@dataclass(frozen=True)
class MediaInfo:
    container: str
    vcodec: str
    width: int
    height: int
    fps: float
    duration_s: float
    has_audio: bool
    acodec: str | None


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")
    return slug or "video"


def _session_id_for(path: Path) -> str:
    digest = _sha256_file(path)[:12]
    return f"external_{_slug(path.stem)[:36]}_{digest}"


def _probe(path: Path) -> MediaInfo:
    proc = subprocess.run(
        [
            "ffprobe", "-v", "error",
            "-print_format", "json",
            "-show_entries",
            "format=duration,format_name:stream=codec_type,codec_name,width,height,avg_frame_rate",
            str(path),
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )
    if proc.returncode != 0:
        sys.exit(f"ffprobe failed on {path}: {proc.stderr.strip()}")

    data = json.loads(proc.stdout)
    streams = data.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    if video is None:
        sys.exit(f"{path}: no video stream found")

    num_s, _, den_s = (video.get("avg_frame_rate") or "0/1").partition("/")
    num = int(num_s) if num_s else 0
    den = int(den_s) if den_s else 1
    fps = num / den if den else 0.0

    return MediaInfo(
        container=data.get("format", {}).get("format_name", "?"),
        vcodec=video.get("codec_name", "?"),
        width=int(video.get("width", 0)),
        height=int(video.get("height", 0)),
        fps=fps,
        duration_s=float(data.get("format", {}).get("duration") or 0),
        has_audio=audio is not None,
        acodec=audio.get("codec_name") if audio else None,
    )


def _needs_normalization(path: Path, info: MediaInfo) -> bool:
    if path.suffix.lower() not in {".mp4", ".mov", ".m4v"}:
        return True
    return info.vcodec != "h264" or info.acodec != "aac"


def _normalize_video(src: Path, dst: Path) -> None:
    proc = subprocess.run(
        [
            "ffmpeg", "-y",
            "-i", str(src),
            "-map", "0:v:0",
            "-map", "0:a:0",
            "-c:v", "libx264",
            "-pix_fmt", "yuv420p",
            "-preset", "veryfast",
            "-crf", "18",
            "-c:a", "aac",
            "-movflags", "+faststart",
            str(dst),
        ],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        console.print(proc.stderr)
        sys.exit("ffmpeg normalization failed; see stderr above")


def _content_type_for(path: Path) -> str:
    if path.suffix.lower() == ".mp4":
        return "video/mp4"
    if path.suffix.lower() in {".mov", ".m4v"}:
        return "video/quicktime"
    return "application/octet-stream"


def _capture_metadata(
    club: str,
    view: str,
    outcome: str | None,
    location: str,
) -> dict:
    tag: dict[str, object] = {"tMs": 0, "club": club, "view": view}
    if outcome:
        tag["outcome"] = outcome
    return {"tagEvents": [tag], "location": location}


def _presign_upload(api: str, user_id: str, session_id: str, content_type: str) -> tuple[str, str]:
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


def _finalize(api: str, user_id: str, session_id: str, metadata: dict) -> str:
    body = _http_post_json(
        f"{api}/api/v1/sessions/{session_id}/finalize",
        {"user_id": user_id, "capture_metadata": metadata},
    )
    return body["workflowId"]


async def _list_session_swings(session_id: str):
    from golf_pipeline.db.client import list_swings_in_session

    return await list_swings_in_session(session_id)


def _print_probe(path: Path, info: MediaInfo, normalized: bool) -> None:
    audio = info.acodec if info.has_audio else "none"
    console.print(
        f"[bold]{path.name}[/bold] "
        f"{info.width}x{info.height}@{info.fps:.2f}fps "
        f"duration={info.duration_s:.2f}s video={info.vcodec} audio={audio} "
        f"normalize={'yes' if normalized else 'no'}"
    )


def _print_dashboard_links(swings, dashboard_base: str) -> None:
    console.print(f"\n[bold green]{len(swings)} swing(s) in Mongo[/bold green]")
    for swing in swings:
        console.print(f"  {swing.id} -> {dashboard_base}/swing/{swing.id}")


async def _main_async(args: argparse.Namespace) -> None:
    video_path = Path(args.video).resolve()
    if not video_path.exists():
        sys.exit(f"file not found: {video_path}")
    if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
        sys.exit("ffmpeg and ffprobe must be on PATH")

    _preflight(args.api)

    from golf_pipeline.config import get_config

    cfg = get_config()
    user_id = args.user_id or cfg.user_id
    session_id = args.session_id or _session_id_for(video_path)
    existing = await _list_session_swings(session_id)
    if existing and not args.force:
        console.print(f"session_id={session_id} already has persisted swings; reusing it.")
        _print_dashboard_links(existing, args.dashboard_base)
        return

    info = _probe(video_path)
    if not info.has_audio:
        sys.exit(f"{video_path.name} has no audio track; audio segmentation cannot run.")

    needs_normalization = _needs_normalization(video_path, info)
    _print_probe(video_path, info, needs_normalization)
    console.print(f"session_id={session_id}")

    with tempfile.TemporaryDirectory() as td:
        upload_path = video_path
        if needs_normalization:
            upload_path = Path(td) / f"{video_path.stem}.normalized.mp4"
            console.log(f"normalizing {video_path.name} -> {upload_path.name}")
            _normalize_video(video_path, upload_path)

        started_at = datetime.now(UTC)
        _register_session(args.api, user_id, session_id, started_at)

        content_type = _content_type_for(upload_path)
        upload_url, s3_key = _presign_upload(args.api, user_id, session_id, content_type)
        console.log(f"s3_key={s3_key}")
        console.log(f"uploading {upload_path.name} as {content_type}")
        _http_put_file(upload_url, upload_path, content_type, timeout=args.upload_timeout)

    metadata = _capture_metadata(
        club=args.club,
        view=args.view,
        outcome=args.outcome,
        location="external-upload",
    )
    try:
        workflow_id = _finalize(args.api, user_id, session_id, metadata)
    except urllib.error.HTTPError as e:
        console.print(
            f"[red]finalize failed:[/red] HTTP {e.code}\n"
            f"Temporal UI: http://localhost:8233/namespaces/default/workflows/session-{session_id}"
        )
        raise

    console.log(f"workflowId={workflow_id}")
    console.print(f"Temporal UI: http://localhost:8233/namespaces/default/workflows/{workflow_id}")
    t0 = time.monotonic()
    try:
        result = await _wait_for_workflow(workflow_id, timeout_s=args.timeout)
    except TimeoutError:
        sys.exit(f"workflow {workflow_id} did not finish in {args.timeout}s")
    elapsed = time.monotonic() - t0

    console.log(f"workflow finished in {elapsed:.1f}s")
    console.print_json(json.dumps(result, default=str))

    swings = await _list_session_swings(session_id)
    if not swings:
        console.print(
            f"\n[yellow]No swings were written for session {session_id}.[/yellow]\n"
            "The workflow completed, but the audio segmenter likely found no impacts."
        )
        sys.exit(2)

    _print_dashboard_links(swings, args.dashboard_base)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("video", help="Path to the external video")
    parser.add_argument("--view", required=True, choices=VALID_VIEWS)
    parser.add_argument("--club", required=True, choices=VALID_CLUBS)
    parser.add_argument("--outcome", choices=VALID_OUTCOMES)
    parser.add_argument("--api", default="http://localhost:8000")
    parser.add_argument("--dashboard-base", default="http://localhost:3000")
    parser.add_argument("--user-id")
    parser.add_argument("--session-id", help="Override content-hash session id")
    parser.add_argument("--force", action="store_true", help="Upload/finalize even if swings exist")
    parser.add_argument("--timeout", type=float, default=1800.0)
    parser.add_argument("--upload-timeout", type=float, default=600.0)
    args = parser.parse_args()
    asyncio.run(_main_async(args))


if __name__ == "__main__":
    main()
