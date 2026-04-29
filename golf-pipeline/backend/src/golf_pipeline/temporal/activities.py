"""Temporal activities — all I/O happens here, never in workflow code.

Each activity is idempotent (keyed by deterministic IDs) and emits structured
logs. Long-running activities heartbeat so Temporal can detect hangs.
"""

from __future__ import annotations

import asyncio
import io
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path

import numpy as np
from temporalio import activity

from golf_pipeline.config import get_config
from golf_pipeline.db.client import (
    append_swing_to_session,
    get_session,
    insert_swing,
    list_swings_in_session,
    upsert_session,
)
from golf_pipeline.metrics.compute import compute_all
from golf_pipeline.schemas import (
    Capture,
    Club,
    IngestRequest,
    KeypointsRef,
    Pipeline,
    Session,
    Swing,
    SwingWindow,
    Tags,
    View,
)
from golf_pipeline.segmentation.audio_impact import segment_video
from golf_pipeline.storage.s3 import (
    download_to_path,
    keypoints_key,
    presign_get,
    raw_video_key,
    upload_bytes,
)


# ─── 1. segment_session_audio ─────────────────────────────────────────────────


@activity.defn
async def segment_session_audio(request: IngestRequest) -> list[SwingWindow]:
    """Pull the session video, segment by audio impacts, return windows."""
    activity.logger.info("Segmenting session %s", request.session_id)

    with tempfile.TemporaryDirectory() as td:
        local_video = Path(td) / "session.mov"
        local_wav = Path(td) / "session.wav"
        await asyncio.to_thread(download_to_path, request.video_s3_key, str(local_video))

        windows = await asyncio.to_thread(segment_video, str(local_video), str(local_wav))

    # decorate each window with metadata from the iOS app (per-swing tags
    # that the app captured live: club, view, outcome). The app sends a list
    # of timestamped tag events; we associate each impact with the closest one.
    tag_events = request.capture_metadata.get("tagEvents", [])
    decorated = []
    for w in windows:
        tag = _closest_tag(tag_events, w.impact_ms)
        decorated.append(
            w.model_copy(
                update={
                    "swing_id": f"{request.session_id}_{w.swing_id}",
                    "club": Club(tag["club"]) if tag.get("club") else None,
                    "view": View(tag["view"]) if tag.get("view") else None,
                    "outcome": tag.get("outcome"),
                    "shape": tag.get("shape"),
                }
            )
        )

    activity.logger.info("Detected %d swings in session %s", len(decorated), request.session_id)

    # ensure the session document exists.
    await upsert_session(
        Session(
            _id=request.session_id,
            userId=request.user_id,
            startedAt=request.captured_at,
            location=request.capture_metadata.get("location"),
        )
    )
    return decorated


def _closest_tag(events: list[dict], t_ms: int) -> dict:
    if not events:
        return {}
    return min(events, key=lambda e: abs(e.get("tMs", 0) - t_ms))


# ─── 2. cut_clip ──────────────────────────────────────────────────────────────


@activity.defn
async def cut_clip(session_id: str, user_id: str, window: SwingWindow) -> str:
    """Cut [start_ms, end_ms] from the session video, upload as a swing clip,
    return its s3:// URI.
    """
    cfg = get_config()
    out_key = raw_video_key(user_id, session_id, window.swing_id)

    with tempfile.TemporaryDirectory() as td:
        local_session = Path(td) / "session.mov"
        local_clip = Path(td) / "clip.mov"

        # the session was already downloaded into a worker-local cache by the
        # segmenter; re-fetch here for idempotency (cheap relative to inference).
        session_key = f"{cfg.aws.prefix_raw}/{user_id}/{session_id}/session.mov"
        await asyncio.to_thread(download_to_path, session_key, str(local_session))

        await asyncio.to_thread(
            _ffmpeg_cut,
            str(local_session),
            str(local_clip),
            window.start_ms / 1000,
            (window.end_ms - window.start_ms) / 1000,
        )

        with open(local_clip, "rb") as f:
            await asyncio.to_thread(upload_bytes, out_key, f.read(), "video/quicktime")

    return f"s3://{cfg.aws.bucket}/{out_key}"


def _ffmpeg_cut(src: str, dst: str, start_s: float, duration_s: float):
    subprocess.run(
        [
            "ffmpeg", "-y",
            "-ss", f"{start_s:.3f}",
            "-i", src,
            "-t", f"{duration_s:.3f}",
            "-c", "copy",
            dst,
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


# ─── 3. run_pose_inference ────────────────────────────────────────────────────


@activity.defn
async def run_pose_inference(
    clip_s3_uri: str, session_id: str, user_id: str, swing_id: str
) -> dict:
    """Call Modal for GPU pose inference. Heartbeats while we wait."""
    cfg = get_config()
    out_key = keypoints_key(user_id, session_id, swing_id)
    out_uri = f"s3://{cfg.aws.bucket}/{out_key}"

    # Import at activity-call time so the worker module doesn't pull modal at startup.
    from golf_pipeline.modal_pose.inference import extract_pose

    activity.heartbeat({"stage": "submitting"})
    fn = extract_pose.remote.aio  # async modal call
    result = await fn(video_s3_uri=clip_s3_uri, out_keypoints_s3_uri=out_uri)
    activity.heartbeat({"stage": "complete", "frames": result["frames"]})
    return result


# ─── 4. compute_metrics_and_write ─────────────────────────────────────────────


@activity.defn
async def compute_metrics_and_write(
    session_id: str,
    user_id: str,
    window: SwingWindow,
    keypoints_s3_uri: str,
    fps: float,
):
    """Download keypoints, compute Tier 1 metrics, write a Swing document."""
    started = datetime.utcnow()

    with tempfile.TemporaryDirectory() as td:
        local_npz = Path(td) / "kp.npz"
        # strip the s3://bucket/ prefix
        key = keypoints_s3_uri.split("/", 3)[-1]
        await asyncio.to_thread(download_to_path, key, str(local_npz))
        kp = np.load(local_npz)["keypoints"]

    # the impact frame is anchored from the audio segmenter — convert ms → frame
    impact_frame = int(round((window.impact_ms - window.start_ms) / 1000 * fps))
    impact_frame = max(0, min(impact_frame, kp.shape[0] - 1))

    phases, metrics, ranges = compute_all(kp, fps=fps, impact_frame=impact_frame)

    cfg = get_config()
    swing = Swing(
        _id=window.swing_id,
        userId=user_id,
        sessionId=session_id,
        createdAt=datetime.utcnow(),
        capture=Capture(
            view=window.view or View.DTL,
            club=window.club or Club.SEVEN_I,
            fps=int(fps),
            resolution=(0, 0),  # populated from session metadata in production
            phoneModel="iPhone16,2",
            videoKey=raw_video_key(user_id, session_id, window.swing_id),
        ),
        tags=Tags(
            outcome=window.outcome,
            shape=window.shape,
        ),
        phases=phases,
        metrics=metrics,
        ranges=ranges,
        keypoints=KeypointsRef(
            schema="blazepose-33",
            fps=int(fps),
            storageRef=keypoints_s3_uri,
        ),
        pipeline=Pipeline(
            version=cfg.pipeline_version,
            poseModel="blazepose-full-v1",
            temporalRunId=activity.info().workflow_run_id,
            processingMs=int((datetime.utcnow() - started).total_seconds() * 1000),
        ),
    )

    await insert_swing(swing)
    await append_swing_to_session(session_id, swing.id)


# ─── 5. summarize_session ─────────────────────────────────────────────────────


@activity.defn
async def summarize_session(session_id: str, user_id: str, completed_swing_ids: list[str]):
    """After all swings in a session are processed, compute session-level summary."""
    swings = await list_swings_in_session(session_id)
    if not swings:
        return

    tempos = [s.metrics.tempo_ratio_backswing_downswing for s in swings if s.metrics.tempo_ratio_backswing_downswing]
    sways = [s.metrics.head_sway_max_mm for s in swings if s.metrics.head_sway_max_mm is not None]

    summary = {
        "tempoRatioMean": float(np.mean(tempos)) if tempos else 0.0,
        "tempoRatioStd": float(np.std(tempos)) if tempos else 0.0,
        "headSwayMeanMm": float(np.mean(sways)) if sways else 0.0,
    }

    session = await get_session(session_id)
    if session is None:
        return
    session = session.model_copy(
        update={
            "ended_at": datetime.utcnow(),
            "swing_count": len(swings),
            "swing_ids": [s.id for s in swings],
            "summary_metrics": summary,
        }
    )
    await upsert_session(session)


# ─── activity registration list (consumed by the worker) ──────────────────────


ALL_ACTIVITIES = [
    segment_session_audio,
    cut_clip,
    run_pose_inference,
    compute_metrics_and_write,
    summarize_session,
]
