"""Pydantic schemas — single source of truth for the data model.

These map 1:1 to the Mongo documents described in PROJECT_SPEC.md.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

# ─── enums ─────────────────────────────────────────────────────────────────────


class View(StrEnum):
    DTL = "DTL"  # down the line
    FO = "FO"  # face on


class Club(StrEnum):
    DRIVER = "driver"
    THREE_W = "3w"
    FIVE_W = "5w"
    HYBRID = "hybrid"
    THREE_I = "3i"
    FOUR_I = "4i"
    FIVE_I = "5i"
    SIX_I = "6i"
    SEVEN_I = "7i"
    EIGHT_I = "8i"
    NINE_I = "9i"
    PW = "pw"
    GW = "gw"
    SW = "sw"
    LW = "lw"
    PUTTER = "putter"


class Outcome(StrEnum):
    GOOD = "good"
    OK = "ok"
    BAD = "bad"


class Shape(StrEnum):
    STRAIGHT = "straight"
    DRAW = "draw"
    FADE = "fade"
    HOOK = "hook"
    SLICE = "slice"
    FAT = "fat"
    THIN = "thin"


# ─── pieces of a swing ─────────────────────────────────────────────────────────


class Capture(BaseModel):
    view: View
    club: Club
    fps: int
    resolution: tuple[int, int]
    phone_model: str = Field(alias="phoneModel")
    video_key: str = Field(alias="videoKey")
    video_expires_at: datetime | None = Field(default=None, alias="videoExpiresAt")

    model_config = ConfigDict(populate_by_name=True)


class Tags(BaseModel):
    outcome: Outcome | None = None
    shape: Shape | None = None
    notes: str | None = None


class PhaseFrame(BaseModel):
    frame: int
    t_ms: int = Field(alias="tMs")

    model_config = ConfigDict(populate_by_name=True)


class Phases(BaseModel):
    address: PhaseFrame
    takeaway: PhaseFrame
    top: PhaseFrame
    transition: PhaseFrame
    impact: PhaseFrame
    finish: PhaseFrame


class Metrics(BaseModel):
    """Tier 1 — deterministic metrics from pose timeseries.

    All angles in degrees, durations in milliseconds, displacements in mm.
    Some fields are view-dependent and will be None when not computable.
    """

    tempo_ratio_backswing_downswing: float | None = Field(
        default=None, alias="tempoRatioBackswingDownswing"
    )
    backswing_duration_ms: int | None = Field(default=None, alias="backswingDurationMs")
    downswing_duration_ms: int | None = Field(default=None, alias="downswingDurationMs")
    shoulder_turn_at_top_deg: float | None = Field(default=None, alias="shoulderTurnAtTopDeg")
    hip_turn_at_top_deg: float | None = Field(default=None, alias="hipTurnAtTopDeg")
    x_factor_deg: float | None = Field(default=None, alias="xFactorDeg")
    wrist_hinge_max_deg: float | None = Field(default=None, alias="wristHingeMaxDeg")
    head_sway_max_mm: float | None = Field(default=None, alias="headSwayMaxMm")
    head_lift_max_mm: float | None = Field(default=None, alias="headLiftMaxMm")
    spine_tilt_at_address_deg: float | None = Field(default=None, alias="spineTiltAtAddressDeg")
    spine_tilt_at_impact_deg: float | None = Field(default=None, alias="spineTiltAtImpactDeg")
    lead_arm_angle_at_top_deg: float | None = Field(default=None, alias="leadArmAngleAtTopDeg")

    model_config = ConfigDict(populate_by_name=True)


class RangeStatus(BaseModel):
    target: tuple[float, float]
    status: Literal["pass", "warn", "fail"]


class Pipeline(BaseModel):
    version: str
    pose_model: str = Field(alias="poseModel")
    modal_run_id: str | None = Field(default=None, alias="modalRunId")
    temporal_run_id: str | None = Field(default=None, alias="temporalRunId")
    processing_ms: int | None = Field(default=None, alias="processingMs")

    model_config = ConfigDict(populate_by_name=True)


class InlineKeypoints(BaseModel):
    """Dual-space inline payload: world coords for biomechanics, image
    coords for the overlay renderer. See `KeypointsRef.schema` for the
    layout version (blazepose-33-v2 stores both)."""

    image: list[list[list[float]]]  # [frame][joint][x_norm, y_norm, vis]
    world: list[list[list[float]]] | None = None  # [frame][joint][x, y, z, vis]


class KeypointsRef(BaseModel):
    """Pose timeseries — usually offloaded to S3; small inline copy is fine for tests."""

    schema_name: str = Field(alias="schema")  # "blazepose-33-v2", "hamer-21", ...
    fps: int
    storage_ref: str | None = Field(default=None, alias="storageRef")  # s3://.../keypoints.npz
    inline: InlineKeypoints | None = None

    model_config = ConfigDict(populate_by_name=True)


# ─── top-level documents ───────────────────────────────────────────────────────


class Swing(BaseModel):
    """A single swing document. Maps to the `swings` Mongo collection."""

    id: str = Field(alias="_id")
    user_id: str = Field(alias="userId")
    session_id: str = Field(alias="sessionId")
    created_at: datetime = Field(alias="createdAt")
    status: Literal["accepted", "rejected"] = "accepted"
    motion_score: float = Field(default=0.0, alias="motionScore")
    capture: Capture
    tags: Tags = Field(default_factory=Tags)
    phases: Phases | None = None
    metrics: Metrics = Field(default_factory=Metrics)
    ranges: dict[str, RangeStatus] = Field(default_factory=dict)
    keypoints: KeypointsRef | None = None
    embedding: list[float] | None = None
    pipeline: Pipeline | None = None

    model_config = ConfigDict(populate_by_name=True)


class Session(BaseModel):
    """Practice session — groups swings together."""

    id: str = Field(alias="_id")
    user_id: str = Field(alias="userId")
    started_at: datetime = Field(alias="startedAt")
    ended_at: datetime | None = Field(default=None, alias="endedAt")
    location: str | None = None
    swing_count: int = Field(default=0, alias="swingCount")
    swing_ids: list[str] = Field(default_factory=list, alias="swingIds")
    summary_metrics: dict[str, float] = Field(default_factory=dict, alias="summaryMetrics")
    notes: str | None = None

    model_config = ConfigDict(populate_by_name=True)


# ─── workflow inputs / outputs ─────────────────────────────────────────────────


class IngestRequest(BaseModel):
    """Inbound from S3 → API → Temporal. One per uploaded session video."""

    user_id: str
    session_id: str
    video_s3_key: str
    captured_at: datetime
    capture_metadata: dict  # raw meta from the iOS app


class SwingWindow(BaseModel):
    """One audio-detected swing window inside a session video."""

    swing_id: str
    start_ms: int
    end_ms: int
    impact_ms: int
    impact_confidence: float
    club: Club | None = None  # carried from app metadata
    view: View | None = None
    outcome: Outcome | None = None
    shape: Shape | None = None
