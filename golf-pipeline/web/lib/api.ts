// Minimal typed fetch wrapper for the FastAPI backend.

import type { KeypointsRef } from "./pose";

export const API_BASE = process.env.NEXT_PUBLIC_API_BASE ?? "http://localhost:8000";

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: { "content-type": "application/json", ...(init?.headers ?? {}) },
    cache: "no-store",
  });
  if (!res.ok) throw new Error(`${res.status} ${res.statusText} on ${path}`);
  return res.json() as Promise<T>;
}

// ─── shared types — mirror the backend schemas ────────────────────────────────

export type RangeStatus = "pass" | "warn" | "fail";

export interface Metrics {
  tempoRatioBackswingDownswing: number | null;
  backswingDurationMs: number | null;
  downswingDurationMs: number | null;
  shoulderTurnAtTopDeg: number | null;
  hipTurnAtTopDeg: number | null;
  xFactorDeg: number | null;
  wristHingeMaxDeg: number | null;
  headSwayMaxMm: number | null;
  headLiftMaxMm: number | null;
  spineTiltAtAddressDeg: number | null;
  spineTiltAtImpactDeg: number | null;
  leadArmAngleAtTopDeg: number | null;
}

export interface Phases {
  address: { frame: number; tMs: number };
  takeaway: { frame: number; tMs: number };
  top: { frame: number; tMs: number };
  transition: { frame: number; tMs: number };
  impact: { frame: number; tMs: number };
  finish: { frame: number; tMs: number };
}

export interface Swing {
  _id: string;
  userId: string;
  sessionId: string;
  createdAt: string;
  status: "accepted" | "rejected";
  motionScore: number;
  capture: {
    view: "DTL" | "FO";
    club: string;
    fps: number;
    resolution: [number, number];
    phoneModel: string;
    videoKey: string;
  };
  tags: { outcome: string | null; shape: string | null; notes: string | null };
  phases: Phases | null;
  metrics: Metrics;
  ranges: Record<string, { target: [number, number]; status: RangeStatus }>;
  videoUrl?: string;
  keypoints?: KeypointsRef | null;
}

export interface Session {
  _id: string;
  userId: string;
  startedAt: string;
  endedAt: string | null;
  location: string | null;
  swingCount: number;
  swingIds: string[];
  summaryMetrics: Record<string, number>;
  notes: string | null;
}

// ─── capture types ────────────────────────────────────────────────────────────

export type View = "DTL" | "FO";
export type Outcome = "good" | "ok" | "bad";
export type Shape = "straight" | "draw" | "fade" | "hook" | "slice" | "fat" | "thin";

export type Club =
  | "driver" | "3w" | "5w" | "hybrid"
  | "3i" | "4i" | "5i" | "6i" | "7i" | "8i" | "9i"
  | "pw" | "gw" | "sw" | "lw" | "putter";

export interface TagEvent {
  tMs: number;
  club?: Club;
  view?: View;
  outcome?: Outcome;
  shape?: Shape;
}

export interface CaptureMetadata {
  location: string | null;
  phoneModel: string;
  fps: number;
  width: number;
  height: number;
  leadSide: "L" | "R";
  tagEvents: TagEvent[];
}

// ─── api functions ────────────────────────────────────────────────────────────

export const listSessions = () => api<Session[]>("/api/v1/sessions");
export const getSession = (id: string) =>
  api<{ session: Session; swings: Swing[] }>(`/api/v1/sessions/${id}`);
export const listSwings = () => api<Swing[]>("/api/v1/swings");
export const getSwing = (id: string) => api<Swing>(`/api/v1/swings/${id}`);

// `[null, null, null]` for joints with NaN landmarks (NaNSafeJSONResponse).
// The frontend already filters with `Number.isFinite`, so this shape is
// safe to hand straight to the SVG overlay.
export interface KeypointsResponse {
  swingId: string;
  schema: string;
  fps: number;
  image: Array<Array<[number, number, number] | [null, null, null]>>;
}

export const getSwingKeypoints = (id: string) =>
  api<KeypointsResponse>(`/api/v1/swings/${id}/keypoints`);

// ─── capture flow ─────────────────────────────────────────────────────────────

export async function startSession(args: {
  userId: string;
  sessionId: string;
  startedAt: Date;
  location?: string;
  notes?: string;
}): Promise<{ ok: boolean; sessionId: string }> {
  return api("/api/v1/sessions", {
    method: "POST",
    body: JSON.stringify({
      user_id: args.userId,
      session_id: args.sessionId,
      started_at: args.startedAt.toISOString(),
      location: args.location ?? null,
      notes: args.notes ?? null,
    }),
  });
}

export async function presignUpload(args: {
  userId: string;
  sessionId: string;
  clipId?: string;
  contentType: string;
}): Promise<{ upload_url: string; s3_key: string }> {
  return api("/api/v1/upload/presign", {
    method: "POST",
    body: JSON.stringify({
      user_id: args.userId,
      session_id: args.sessionId,
      clip_id: args.clipId ?? "session",
      content_type: args.contentType,
    }),
  });
}

export async function finalizeSession(args: {
  sessionId: string;
  userId: string;
  captureMetadata: CaptureMetadata;
}): Promise<{ ok: boolean; workflowId: string }> {
  return api(`/api/v1/sessions/${args.sessionId}/finalize`, {
    method: "POST",
    body: JSON.stringify({
      user_id: args.userId,
      capture_metadata: args.captureMetadata,
    }),
  });
}

export async function putToS3(
  presignedUrl: string,
  blob: Blob,
  onProgress?: (loaded: number, total: number) => void,
): Promise<void> {
  // Use XHR rather than fetch — fetch has no upload-progress events.
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("PUT", presignedUrl);
    xhr.setRequestHeader("Content-Type", blob.type);
    if (onProgress) {
      xhr.upload.onprogress = (e) => {
        if (e.lengthComputable) onProgress(e.loaded, e.total);
      };
    }
    xhr.onload = () =>
      xhr.status >= 200 && xhr.status < 300
        ? resolve()
        : reject(new Error(`S3 upload failed: ${xhr.status} ${xhr.statusText}`));
    xhr.onerror = () => reject(new Error("S3 upload network error"));
    xhr.send(blob);
  });
}
