"use client";

import { useEffect, useRef, useState } from "react";
import {
  type CaptureMetadata,
  type Club,
  type Outcome,
  type Shape,
  type TagEvent,
  type View,
  finalizeSession,
  presignUpload,
  putToS3,
  startSession,
} from "@/lib/api";
import {
  Recorder,
  acquireStream,
  detectPhoneModel,
  newSessionId,
  requestWakeLock,
  streamCapabilities,
} from "@/lib/capture";
import { ClubPicker } from "@/components/capture/ClubPicker";
import { TagPanel } from "@/components/capture/TagPanel";

const USER_ID = "trey";
const LEAD_SIDE: "L" | "R" = "L";

type State =
  | { kind: "idle" }
  | { kind: "ready" }
  | { kind: "recording"; startedAt: number }
  | { kind: "uploading"; progress: number }
  | { kind: "done"; sessionId: string; workflowId: string }
  | { kind: "error"; message: string };

export default function CapturePage() {
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const recorderRef = useRef<Recorder | null>(null);
  const wakeLockRef = useRef<{ release(): Promise<void> } | null>(null);
  const sessionIdRef = useRef<string>("");

  const [state, setState] = useState<State>({ kind: "idle" });
  const [elapsedMs, setElapsedMs] = useState(0);
  const [club, setClub] = useState<Club>("7i");
  const [view, setView] = useState<View>("DTL");
  const [tagEvents, setTagEvents] = useState<TagEvent[]>([]);
  const [caps, setCaps] = useState<{ width: number; height: number; fps: number } | null>(null);

  // ─── camera bring-up ────────────────────────────────────────────────────────

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const stream = await acquireStream({ width: 1920, height: 1080, frameRate: 60 });
        if (cancelled) {
          stream.getTracks().forEach((t) => t.stop());
          return;
        }
        streamRef.current = stream;
        if (videoRef.current) {
          videoRef.current.srcObject = stream;
          await videoRef.current.play().catch(() => {});
        }
        const c = streamCapabilities(stream);
        setCaps({ width: c.width, height: c.height, fps: c.frameRate });
        setState({ kind: "ready" });
      } catch (e: unknown) {
        const msg = e instanceof Error ? e.message : "Camera unavailable";
        setState({ kind: "error", message: msg });
      }
    })();
    return () => {
      cancelled = true;
      streamRef.current?.getTracks().forEach((t) => t.stop());
      streamRef.current = null;
      wakeLockRef.current?.release().catch(() => {});
    };
  }, []);

  // ─── elapsed timer while recording ──────────────────────────────────────────

  useEffect(() => {
    if (state.kind !== "recording") return;
    const startedAt = state.startedAt;
    const id = window.setInterval(() => setElapsedMs(Date.now() - startedAt), 100);
    return () => window.clearInterval(id);
  }, [state.kind === "recording" ? state.startedAt : 0]); // eslint-disable-line react-hooks/exhaustive-deps

  // ─── controls ───────────────────────────────────────────────────────────────

  async function handleStart() {
    if (!streamRef.current) return;
    try {
      sessionIdRef.current = newSessionId();
      await startSession({
        userId: USER_ID,
        sessionId: sessionIdRef.current,
        startedAt: new Date(),
      });

      const recorder = new Recorder(streamRef.current);
      recorder.start(8_000_000);
      recorderRef.current = recorder;

      wakeLockRef.current = await requestWakeLock();

      setTagEvents([]);
      setElapsedMs(0);
      setState({ kind: "recording", startedAt: Date.now() });
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : "Failed to start";
      setState({ kind: "error", message: msg });
    }
  }

  async function handleStop() {
    if (!recorderRef.current || state.kind !== "recording") return;
    try {
      const blob = await recorderRef.current.stop();
      recorderRef.current = null;
      await wakeLockRef.current?.release().catch(() => {});
      wakeLockRef.current = null;

      setState({ kind: "uploading", progress: 0 });

      const presign = await presignUpload({
        userId: USER_ID,
        sessionId: sessionIdRef.current,
        clipId: "session",
        contentType: blob.type || "video/mp4",
      });

      await putToS3(presign.upload_url, blob, (loaded, total) => {
        setState({ kind: "uploading", progress: loaded / total });
      });

      const metadata: CaptureMetadata = {
        location: null,
        phoneModel: detectPhoneModel(),
        fps: caps?.fps ?? 60,
        width: caps?.width ?? 1920,
        height: caps?.height ?? 1080,
        leadSide: LEAD_SIDE,
        tagEvents,
      };

      const result = await finalizeSession({
        sessionId: sessionIdRef.current,
        userId: USER_ID,
        captureMetadata: metadata,
      });

      setState({
        kind: "done",
        sessionId: sessionIdRef.current,
        workflowId: result.workflowId,
      });
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : "Upload failed";
      setState({ kind: "error", message: msg });
    }
  }

  function tagSwing(outcome: Outcome | null, shape: Shape | null) {
    if (state.kind !== "recording") return;
    setTagEvents((prev) => [
      ...prev,
      {
        tMs: Date.now() - state.startedAt,
        club,
        view,
        outcome: outcome ?? undefined,
        shape: shape ?? undefined,
      },
    ]);
  }

  function emitMetadataTag(next: { club?: Club; view?: View }) {
    if (state.kind !== "recording") return;
    setTagEvents((prev) => [
      ...prev,
      { tMs: Date.now() - state.startedAt, club: next.club ?? club, view: next.view ?? view },
    ]);
  }

  function newSession() {
    setState({ kind: "ready" });
    setTagEvents([]);
    setElapsedMs(0);
  }

  // ─── render ─────────────────────────────────────────────────────────────────

  const isRecording = state.kind === "recording";
  const elapsedStr = formatElapsed(elapsedMs);

  return (
    <div className="capture-root fixed inset-0 bg-black overflow-hidden">
      {/* video preview fills the screen */}
      <video
        ref={videoRef}
        playsInline
        muted
        autoPlay
        className="absolute inset-0 w-full h-full object-cover"
      />

      {/* dark vignette so controls are legible */}
      <div className="pointer-events-none absolute inset-0 bg-gradient-to-b from-black/60 via-transparent to-black/70" />

      {/* top bar */}
      <div className="absolute top-0 inset-x-0 p-4 flex items-center justify-between safe-top">
        <StatePill state={state} />
        <div className="flex items-center gap-2 px-3 py-1.5 rounded-sm bg-black/60 border border-white/10">
          <span className={`w-1.5 h-1.5 rounded-full ${isRecording ? "bg-signal-red animate-pulse" : "bg-white/30"}`} />
          <span className="font-mono text-sm font-bold text-white num">{elapsedStr}</span>
          {caps && (
            <span className="font-mono text-[10px] text-white/40 ml-2 num">
              {caps.height}p · {Math.round(caps.fps)}fps
            </span>
          )}
        </div>
      </div>

      {/* main overlay states */}
      {state.kind === "uploading" && <UploadingOverlay progress={state.progress} />}
      {state.kind === "done" && <DoneOverlay sessionId={state.sessionId} onReset={newSession} />}
      {state.kind === "error" && <ErrorOverlay message={state.message} />}

      {/* bottom controls */}
      {(state.kind === "ready" || state.kind === "recording") && (
        <div className="absolute bottom-0 inset-x-0 p-4 pb-8 space-y-3 safe-bottom">
          {isRecording && <TagPanel tagCount={tagEvents.length} onTag={tagSwing} />}

          <div className="flex items-center gap-3 justify-between">
            <button
              onClick={() => {
                const next: View = view === "DTL" ? "FO" : "DTL";
                setView(next);
                emitMetadataTag({ view: next });
              }}
              className="font-mono text-[11px] font-bold uppercase tracking-wider2 w-14 h-8 rounded-sm border border-white/30 text-white"
            >
              {view}
            </button>

            <ClubPicker
              selected={club}
              onChange={(c) => {
                setClub(c);
                emitMetadataTag({ club: c });
              }}
            />

            <button
              onClick={isRecording ? handleStop : handleStart}
              disabled={state.kind !== "ready" && !isRecording}
              className={`font-mono text-sm font-bold uppercase tracking-wider2 w-24 h-11 rounded-sm ${
                isRecording ? "bg-signal-red text-black" : "bg-accent text-black"
              }`}
            >
              {isRecording ? "End" : "Start"}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

// ─── pieces ───────────────────────────────────────────────────────────────────

function StatePill({ state }: { state: State }) {
  const map: Record<State["kind"], { label: string; bg: string; text: string }> = {
    idle: { label: "BOOT", bg: "bg-white/30", text: "text-black" },
    ready: { label: "READY", bg: "bg-signal-green", text: "text-black" },
    recording: { label: "REC", bg: "bg-signal-red", text: "text-black" },
    uploading: { label: "UPLOADING", bg: "bg-signal-amber", text: "text-black" },
    done: { label: "DONE", bg: "bg-accent", text: "text-black" },
    error: { label: "ERROR", bg: "bg-signal-red", text: "text-black" },
  };
  const p = map[state.kind];
  return (
    <div
      className={`font-mono text-[10px] font-bold tracking-wider2 px-2 py-1 rounded-sm ${p.bg} ${p.text}`}
    >
      {p.label}
    </div>
  );
}

function UploadingOverlay({ progress }: { progress: number }) {
  const pct = Math.round(progress * 100);
  return (
    <div className="absolute inset-0 flex items-center justify-center bg-black/70">
      <div className="text-center space-y-3">
        <div className="font-display text-3xl tracking-tight">Uploading…</div>
        <div className="w-64 h-1 bg-white/15 rounded-full overflow-hidden mx-auto">
          <div
            className="h-full bg-accent transition-[width] duration-200"
            style={{ width: `${pct}%` }}
          />
        </div>
        <div className="font-mono text-xs text-white/50 num">{pct}%</div>
      </div>
    </div>
  );
}

function DoneOverlay({ sessionId, onReset }: { sessionId: string; onReset: () => void }) {
  return (
    <div className="absolute inset-0 flex items-center justify-center bg-black/80">
      <div className="text-center space-y-4 px-6">
        <div className="font-display text-3xl tracking-tight">Session uploaded</div>
        <div className="font-mono text-xs text-white/50 break-all">{sessionId}</div>
        <div className="font-mono text-[11px] text-white/60 max-w-xs mx-auto leading-relaxed">
          The Temporal workflow is now segmenting impacts and running pose. Check the dashboard in a few minutes.
        </div>
        <div className="flex gap-2 justify-center pt-2">
          <button
            onClick={onReset}
            className="font-mono text-xs font-bold uppercase tracking-wider2 bg-accent text-black px-5 h-10 rounded-sm"
          >
            New session
          </button>
          <a
            href={`/sessions/${sessionId}`}
            className="font-mono text-xs font-bold uppercase tracking-wider2 border border-white/30 text-white px-5 h-10 rounded-sm flex items-center"
          >
            View →
          </a>
        </div>
      </div>
    </div>
  );
}

function ErrorOverlay({ message }: { message: string }) {
  return (
    <div className="absolute inset-0 flex items-center justify-center bg-black/85 px-6">
      <div className="text-center space-y-3 max-w-sm">
        <div className="font-display text-2xl text-signal-red">Something broke</div>
        <div className="font-mono text-xs text-white/70 break-words">{message}</div>
        <div className="font-mono text-[10px] text-white/40 leading-relaxed pt-2">
          Common fixes: check that the API is reachable, that you accepted the camera prompt,
          and that you&apos;re on HTTPS (camera access requires it on iOS).
        </div>
        <button
          onClick={() => location.reload()}
          className="font-mono text-xs font-bold uppercase tracking-wider2 bg-white/10 text-white px-5 h-9 rounded-sm mt-3"
        >
          Reload
        </button>
      </div>
    </div>
  );
}

function formatElapsed(ms: number): string {
  const totalSec = Math.floor(ms / 1000);
  const m = Math.floor(totalSec / 60);
  const s = totalSec % 60;
  return `${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
}
