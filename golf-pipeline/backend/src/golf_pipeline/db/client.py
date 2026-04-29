"""MongoDB access — Motor (async) for app code, Pymongo for sync scripts."""

from __future__ import annotations

from datetime import datetime
from functools import lru_cache

from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase
from pymongo import ASCENDING, DESCENDING

from golf_pipeline.config import get_config
from golf_pipeline.schemas import Session, Swing


@lru_cache
def _client() -> AsyncIOMotorClient:
    cfg = get_config()
    return AsyncIOMotorClient(cfg.mongo.uri)


def db() -> AsyncIOMotorDatabase:
    return _client()[get_config().mongo.db]


# ─── one-time index setup ──────────────────────────────────────────────────────


async def ensure_indexes():
    swings = db().swings
    sessions = db().sessions

    await swings.create_index([("userId", ASCENDING), ("createdAt", DESCENDING)])
    await swings.create_index(
        [("userId", ASCENDING), ("capture.club", ASCENDING), ("createdAt", DESCENDING)]
    )
    await swings.create_index([("userId", ASCENDING), ("tags.outcome", ASCENDING)])
    await swings.create_index([("sessionId", ASCENDING)])

    await sessions.create_index([("userId", ASCENDING), ("startedAt", DESCENDING)])

    # Atlas vector index on `embedding` is created via Atlas UI / CLI;
    # see docs/atlas-vector-index.md.


# ─── swing repository ─────────────────────────────────────────────────────────


async def insert_swing(swing: Swing) -> str:
    doc = swing.model_dump(by_alias=True, exclude_none=False)
    await db().swings.insert_one(doc)
    return swing.id


async def get_swing(swing_id: str) -> Swing | None:
    doc = await db().swings.find_one({"_id": swing_id})
    return Swing.model_validate(doc) if doc else None


async def list_recent_swings(user_id: str, limit: int = 50) -> list[Swing]:
    cursor = (
        db()
        .swings.find({"userId": user_id})
        .sort("createdAt", DESCENDING)
        .limit(limit)
    )
    return [Swing.model_validate(d) async for d in cursor]


async def list_swings_in_session(session_id: str) -> list[Swing]:
    cursor = db().swings.find({"sessionId": session_id}).sort("createdAt", ASCENDING)
    return [Swing.model_validate(d) async for d in cursor]


async def find_similar_swings(
    embedding: list[float], user_id: str, k: int = 5, exclude_id: str | None = None
) -> list[Swing]:
    """Mongo Atlas vector search. Requires a vector index named `swing_embeddings`."""
    pipeline = [
        {
            "$vectorSearch": {
                "index": "swing_embeddings",
                "path": "embedding",
                "queryVector": embedding,
                "numCandidates": 200,
                "limit": k + 1,
                "filter": {"userId": user_id},
            }
        },
    ]
    if exclude_id:
        pipeline.append({"$match": {"_id": {"$ne": exclude_id}}})
    pipeline.append({"$limit": k})

    cursor = db().swings.aggregate(pipeline)
    return [Swing.model_validate(d) async for d in cursor]


# ─── session repository ────────────────────────────────────────────────────────


async def upsert_session(session: Session) -> str:
    doc = session.model_dump(by_alias=True, exclude_none=False)
    await db().sessions.replace_one({"_id": session.id}, doc, upsert=True)
    return session.id


async def get_session(session_id: str) -> Session | None:
    doc = await db().sessions.find_one({"_id": session_id})
    return Session.model_validate(doc) if doc else None


async def list_recent_sessions(user_id: str, limit: int = 30) -> list[Session]:
    cursor = (
        db()
        .sessions.find({"userId": user_id})
        .sort("startedAt", DESCENDING)
        .limit(limit)
    )
    return [Session.model_validate(d) async for d in cursor]


async def append_swing_to_session(session_id: str, swing_id: str):
    await db().sessions.update_one(
        {"_id": session_id},
        {
            "$addToSet": {"swingIds": swing_id},
            "$inc": {"swingCount": 1},
            "$set": {"updatedAt": datetime.utcnow()},
        },
    )
