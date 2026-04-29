"""Temporal workflow definitions.

Two workflows:

  ProcessSession  — runs once per uploaded session video. Segments by audio,
                    spawns a child workflow per detected swing, then summarizes.

  ProcessSwing    — per-swing pipeline: pose inference → metrics → DB write.

Workflow code is deterministic-only — all I/O lives in activities.
"""

from __future__ import annotations

from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from golf_pipeline.schemas import IngestRequest, SwingWindow


# ─── ProcessSwing (child) ──────────────────────────────────────────────────────


@workflow.defn
class ProcessSwing:
    """Pose, metrics, embedding, DB write for one swing window."""

    @workflow.run
    async def run(self, session_id: str, user_id: str, window: SwingWindow) -> str:
        retry = RetryPolicy(
            initial_interval=timedelta(seconds=2),
            maximum_interval=timedelta(seconds=30),
            maximum_attempts=4,
        )

        # 1. Cut the swing clip from the session video and upload.
        clip_uri = await workflow.execute_activity(
            "cut_clip",
            args=[session_id, user_id, window],
            schedule_to_close_timeout=timedelta(minutes=2),
            retry_policy=retry,
        )

        # 2. Pose inference on Modal (long-running, with heartbeats).
        pose_result = await workflow.execute_activity(
            "run_pose_inference",
            args=[clip_uri, session_id, user_id, window.swing_id],
            schedule_to_close_timeout=timedelta(minutes=8),
            heartbeat_timeout=timedelta(seconds=30),
            retry_policy=retry,
        )

        # 3. Compute metrics from pose timeseries (uses audio-anchored impact frame).
        await workflow.execute_activity(
            "compute_metrics_and_write",
            args=[
                session_id,
                user_id,
                window,
                pose_result["keypoints_uri"],
                pose_result["fps"],
            ],
            schedule_to_close_timeout=timedelta(minutes=2),
            retry_policy=retry,
        )

        return window.swing_id


# ─── ProcessSession (parent) ───────────────────────────────────────────────────


@workflow.defn
class ProcessSession:
    @workflow.run
    async def run(self, request: IngestRequest) -> dict:
        retry = RetryPolicy(
            initial_interval=timedelta(seconds=2),
            maximum_interval=timedelta(seconds=30),
            maximum_attempts=4,
        )

        # 1. Probe + segment by audio.
        windows: list[SwingWindow] = await workflow.execute_activity(
            "segment_session_audio",
            args=[request],
            schedule_to_close_timeout=timedelta(minutes=10),
            retry_policy=retry,
        )

        if not windows:
            return {"sessionId": request.session_id, "swingCount": 0, "swings": []}

        # 2. Spawn a child workflow per swing — independent failure domains.
        child_handles = []
        for w in windows:
            handle = await workflow.start_child_workflow(
                ProcessSwing.run,
                args=[request.session_id, request.user_id, w],
                id=f"swing-{request.session_id}-{w.swing_id}",
            )
            child_handles.append(handle)

        # 3. Wait for all children. Use return_when=ALL_COMPLETED semantics:
        completed: list[str] = []
        failed: list[tuple[str, str]] = []
        for h in child_handles:
            try:
                result = await h
                completed.append(result)
            except Exception as e:  # noqa: BLE001 — we want to keep going
                failed.append((str(h.id), repr(e)))
                workflow.logger.warning("Swing child failed: %s", e)

        # 4. Summarize the session.
        await workflow.execute_activity(
            "summarize_session",
            args=[request.session_id, request.user_id, completed],
            schedule_to_close_timeout=timedelta(minutes=2),
            retry_policy=retry,
        )

        return {
            "sessionId": request.session_id,
            "swingCount": len(completed),
            "swings": completed,
            "failed": failed,
        }
