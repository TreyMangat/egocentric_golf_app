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
GET   /api/v1/swings/:id/similar  → vector-search similar swings (V1.5)
"""

from __future__ import annotations

import os
from datetime import datetime
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from temporalio.client import Client

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
from golf_pipeline.storage.s3 import presign_get, presign_put, raw_video_key
from golf_pipeline.temporal.workflows import ProcessSession

cfg = get_config()
app = FastAPI(title="golf-pipeline API", version=cfg.pipeline_version)

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

    client = await Client.connect(cfg.temporal.target, namespace=cfg.temporal.namespace)
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
        out["videoUrl"] = presign_get(s.capture.video_key)
    return out


@app.get("/api/v1/swings/{swing_id}/similar")
async def swing_similar(swing_id: str, k: int = 5):
    s = await get_swing(swing_id)
    if s is None:
        raise HTTPException(404)
    if not s.embedding:
        raise HTTPException(409, "swing has no embedding yet (V1.5 feature)")
    similar = await find_similar_swings(s.embedding, cfg.user_id, k=k, exclude_id=swing_id)
    return [s.model_dump(by_alias=True) for s in similar]
