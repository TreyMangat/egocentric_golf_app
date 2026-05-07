"""FastAPI server.

Endpoints
---------
GET   /healthz
POST  /api/v1/upload/presign    → presigned PUT for the iOS app
POST  /api/v1/sessions          → register a session start (called by iOS)
POST  /api/v1/sessions/:id/finalize  → triggers ProcessSession workflow
GET   /api/v1/sessions          → list recent sessions
GET   /api/v1/sessions/:id      → session detail
GET   /api/v1/swings            → list recent swings
GET   /api/v1/swings/:id        → swing detail
GET   /api/v1/swings/:id/keypoints  → image-normalized landmark series
GET   /api/v1/swings/:id/similar  → vector-search similar swings (V1.5)
"""

from __future__ import annotations

import asyncio
import json
import math
import os
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from temporalio.client import Client
from temporalio.contrib.pydantic import pydantic_data_converter

from golf_pipeline.config import get_config
from golf_pipeline.db.client import (
    ensure_indexes,
    find_similar_swings,
    get_session,
    get_swing,
    list_recent_sessions,
    list_recent_swings,
    list_swings_in_session,
    upsert_session,
)
from golf_pipeline.schemas import IngestRequest, Session
from golf_pipeline.storage.s3 import (
    download_to_path,
    parse_s3_uri,
    presign_get,
    presign_put,
    raw_video_key,
)
from golf_pipeline.temporal.workflows import ProcessSession


def _nan_to_none(o: Any) -> Any:
    if isinstance(o, float):
        return None if math.isnan(o) or math.isinf(o) else o
    if isinstance(o, dict):
        return {k: _nan_to_none(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_nan_to_none(v) for v in o]
    return o


class NaNSafeJSONResponse(JSONResponse):
    """JSON response that maps float NaN/Inf → null. Smoke/black-frame metrics
    legitimately produce NaN; the default encoder rejects them with allow_nan=False
    and surfaces a bare 500 to clients."""

    def render(self, content: Any) -> bytes:
        return json.dumps(
            _nan_to_none(content),
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        ).encode("utf-8")


cfg = get_config()
S3_VIDEO_URL_EXPIRES_SECONDS = 7 * 24 * 60 * 60
app = FastAPI(
    title="golf-pipeline API",
    version=cfg.pipeline_version,
    default_response_class=NaNSafeJSONResponse,
)

origins = os.getenv("ALLOWED_ORIGINS", "http://localhost:3000").split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ─── lifecycle ────────────────────────────────────────────────────────────────


@app.on_event("startup")
async def _startup():
    await ensure_indexes()


@app.get("/healthz")
async def healthz():
    return {"ok": True, "version": cfg.pipeline_version}


# ─── upload presign ───────────────────────────────────────────────────────────


class PresignRequest(BaseModel):
    user_id: str
    session_id: str
    clip_id: str = "session"  # for V1, the whole session uploads as one clip
    content_type: str = "video/quicktime"


class PresignResponse(BaseModel):
    upload_url: str
    s3_key: str


@app.post("/api/v1/upload/presign", response_model=PresignResponse)
async def upload_presign(body: PresignRequest):
    key = raw_video_key(body.user_id, body.session_id, body.clip_id)
    url = presign_put(key, content_type=body.content_type)
    return PresignResponse(upload_url=url, s3_key=key)


# ─── sessions ─────────────────────────────────────────────────────────────────


class StartSessionRequest(BaseModel):
    user_id: str
    session_id: str
    started_at: datetime
    location: str | None = None
    notes: str | None = None


@app.post("/api/v1/sessions")
async def start_session(body: StartSessionRequest):
    sess = Session(
        _id=body.session_id,
        userId=body.user_id,
        startedAt=body.started_at,
        location=body.location,
        notes=body.notes,
    )
    await upsert_session(sess)
    return {"ok": True, "sessionId": body.session_id}


class FinalizeRequest(BaseModel):
    user_id: str
    capture_metadata: dict[str, Any]


@app.post("/api/v1/sessions/{session_id}/finalize")
async def finalize_session(session_id: str, body: FinalizeRequest):
    sess = await get_session(session_id)
    if sess is None:
        raise HTTPException(404, "session not found")

    request = IngestRequest(
        user_id=body.user_id,
        session_id=session_id,
        video_s3_key=raw_video_key(body.user_id, session_id, "session"),
        captured_at=sess.started_at,
        capture_metadata=body.capture_metadata,
    )

    client = await Client.connect(
        cfg.temporal.target,
        namespace=cfg.temporal.namespace,
        data_converter=pydantic_data_converter,
    )
    handle = await client.start_workflow(
        ProcessSession.run,
        request,
        id=f"session-{session_id}",
        task_queue=cfg.temporal.task_queue,
    )
    return {"ok": True, "workflowId": handle.id}


@app.get("/api/v1/sessions")
async def list_sessions():
    sessions = await list_recent_sessions(cfg.user_id)
    return [s.model_dump(by_alias=True) for s in sessions]


@app.get("/api/v1/sessions/{session_id}")
async def session_detail(session_id: str):
    sess = await get_session(session_id)
    if sess is None:
        raise HTTPException(404)
    swings = await list_swings_in_session(session_id)
    return {
        "session": sess.model_dump(by_alias=True),
        "swings": [s.model_dump(by_alias=True) for s in swings],
    }


# ─── swings ───────────────────────────────────────────────────────────────────


@app.get("/api/v1/swings")
async def list_swings():
    swings = await list_recent_swings(cfg.user_id)
    return [s.model_dump(by_alias=True) for s in swings]


@app.get("/api/v1/swings/{swing_id}")
async def swing_detail(swing_id: str):
    s = await get_swing(swing_id)
    if s is None:
        raise HTTPException(404)
    out = s.model_dump(by_alias=True)
    if s.capture.video_key:
        out["videoUrl"] = presign_get(
            s.capture.video_key,
            expires_seconds=S3_VIDEO_URL_EXPIRES_SECONDS,
        )
    return out


def _load_keypoints_image(storage_ref: str) -> tuple[list[list[list[float]]], float]:
    """Download a swing's keypoints `.npz` from S3 and return its
    image-normalized landmarks plus fps. Pure helper — no Mongo, no app
    state, so it stands alone in tests.

    `keypoints_image` is shaped (frames, 33, 3) where each row is
    (x_norm, y_norm, visibility). NaNs survive into the JSON payload as
    `null` via `NaNSafeJSONResponse`; the frontend already filters those
    out via `isUsableJoint`.
    """
    _, key = parse_s3_uri(storage_ref)
    with tempfile.TemporaryDirectory() as td:
        local = Path(td) / "kp.npz"
        download_to_path(key, str(local))
        # On Windows np.load keeps a file handle until close; the `with`
        # form releases before tempdir cleanup.
        with np.load(local) as npz:
            image = np.array(npz["keypoints_image"], dtype=np.float64)
            fps = float(npz["fps"])
    return image.tolist(), fps


@app.get("/api/v1/swings/{swing_id}/keypoints")
async def swing_keypoints(swing_id: str):
    """Return the image-normalized BlazePose landmark series for a swing.

    Used by the swing-detail page to drive the SVG skeleton overlay over
    the rendered video. The keypoints `.npz` lives on S3; we download once
    per request and emit JSON. Cache headers are conservative — the file
    is immutable per swing, but in V1 we'd rather refetch than risk
    serving stale frames behind a long-lived CDN entry.
    """
    s = await get_swing(swing_id)
    if s is None:
        raise HTTPException(404)
    if s.keypoints is None or not s.keypoints.storage_ref:
        # No offloaded keypoints to fetch (rejected swings, legacy docs).
        # 404 keeps the client-side branch simple: present → render, absent
        # → fall back to the existing "no overlay" UI.
        raise HTTPException(404, "swing has no offloaded keypoints")

    image, fps = await asyncio.to_thread(_load_keypoints_image, s.keypoints.storage_ref)

    return NaNSafeJSONResponse(
        content={
            "swingId": swing_id,
            "schema": s.keypoints.schema_name,
            "fps": int(round(fps)),
            "image": image,
        },
        headers={"Cache-Control": "private, max-age=300"},
    )


@app.get("/api/v1/swings/{swing_id}/similar")
async def swing_similar(swing_id: str, k: int = 5):
    s = await get_swing(swing_id)
    if s is None:
        raise HTTPException(404)
    if not s.embedding:
        raise HTTPException(409, "swing has no embedding yet (V1.5 feature)")
    similar = await find_similar_swings(s.embedding, cfg.user_id, k=k, exclude_id=swing_id)
    return [s.model_dump(by_alias=True) for s in similar]
