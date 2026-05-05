"use client";

import {
  forwardRef,
  useEffect,
  useImperativeHandle,
  useRef,
  useState,
} from "react";
import type { Phases } from "@/lib/api";
import {
  POSE_CONNECTIONS,
  VIS_HIGH,
  VIS_MIN,
  type ImageFrame,
  type ImageJoint,
  type ImageKeypointSeries,
  type KeypointsRef,
} from "@/lib/pose";

interface Props {
  videoUrl: string | undefined;
  resolution: [number, number];
  view: string;
  club: string;
  /** Capture fps — drives ±1 frame keyboard stepping. */
  fps: number;
  keypoints: KeypointsRef | null | undefined;
  phases: Phases | null | undefined;
  motionScore?: number;
  status?: "accepted" | "rejected";
  /** Cross-player sync hooks (compare view). All optional. */
  onPlay?: () => void;
  onPause?: () => void;
  onSeek?: (currentTime: number) => void;
  /** When true, hides the keyboard listener so two SwingPlayers on one
   *  page don't both react to the same arrow press. */
  disableKeyboard?: boolean;
}

export interface SwingPlayerHandle {
  play: () => void;
  pause: () => void;
  seekTo: (seconds: number) => void;
  getCurrentTime: () => number;
  isPaused: () => boolean;
}

type PhaseEntry = [name: string, marker: { frame: number; tMs: number }];

function orderedPhases(phases: Phases | null | undefined): PhaseEntry[] {
  if (!phases) return [];
  // Object.entries preserves insertion order, but the API result is a dict
  // and we'd rather not bet that the JSON encoder keeps the schema's order
  // forever. Sort by tMs so the row reads address → finish regardless.
  return (Object.entries(phases) as PhaseEntry[]).sort(
    (a, b) => a[1].tMs - b[1].tMs,
  );
}

const SVG_NS = "http://www.w3.org/2000/svg";

// Solid hex; rgba/oklch aren't accepted by the SVG `stroke`/`fill` attribute
// in some browsers, so we keep these as plain hex and rely on `opacity` for
// the faded look.
const ACCENT = "#d4ff5a";
const FADED = "#e8ebf2"; // ink-100

function isUsableJoint(j: ImageJoint | undefined | null): j is ImageJoint {
  return (
    Array.isArray(j) &&
    j.length >= 3 &&
    Number.isFinite(j[0]) &&
    Number.isFinite(j[1]) &&
    Number.isFinite(j[2])
  );
}

function drawFrame(
  svg: SVGSVGElement,
  joints: ImageFrame | undefined,
  width: number,
  height: number,
) {
  // Imperative wipe + redraw. setState 60×/sec would thrash React's reconciler
  // for no benefit — the SVG tree here is small and short-lived.
  while (svg.firstChild) svg.removeChild(svg.firstChild);
  if (!joints) return;

  for (const [a, b] of POSE_CONNECTIONS) {
    const ja = joints[a];
    const jb = joints[b];
    if (!isUsableJoint(ja) || !isUsableJoint(jb)) continue;
    if (ja[2] < VIS_MIN || jb[2] < VIS_MIN) continue;
    const lowConfidence = ja[2] < VIS_HIGH || jb[2] < VIS_HIGH;
    const line = document.createElementNS(SVG_NS, "line");
    line.setAttribute("x1", String(ja[0] * width));
    line.setAttribute("y1", String(ja[1] * height));
    line.setAttribute("x2", String(jb[0] * width));
    line.setAttribute("y2", String(jb[1] * height));
    line.setAttribute("stroke", lowConfidence ? FADED : ACCENT);
    line.setAttribute("stroke-opacity", lowConfidence ? "0.4" : "0.9");
    line.setAttribute("stroke-width", "1.5");
    line.setAttribute("stroke-linecap", "round");
    svg.appendChild(line);
  }

  for (let i = 0; i < joints.length; i++) {
    const j = joints[i];
    if (!isUsableJoint(j)) continue;
    if (j[2] < VIS_MIN) continue;
    const high = j[2] >= VIS_HIGH;
    const c = document.createElementNS(SVG_NS, "circle");
    c.setAttribute("cx", String(j[0] * width));
    c.setAttribute("cy", String(j[1] * height));
    c.setAttribute("r", high ? "2.5" : "2");
    c.setAttribute("fill", high ? ACCENT : FADED);
    c.setAttribute("fill-opacity", high ? "1" : "0.5");
    svg.appendChild(c);
  }
}

export const SwingPlayer = forwardRef<SwingPlayerHandle, Props>(function SwingPlayer({
  videoUrl,
  resolution,
  view,
  club,
  fps,
  keypoints,
  phases,
  motionScore,
  status = "accepted",
  onPlay,
  onPause,
  onSeek,
  disableKeyboard,
}, ref) {
  const videoRef = useRef<HTMLVideoElement>(null);
  const svgRef = useRef<SVGSVGElement>(null);
  const rafRef = useRef<number | null>(null);
  const scrubberRef = useRef<HTMLDivElement>(null);
  const playheadRef = useRef<HTMLDivElement>(null);
  // Seeded once `loadedmetadata` fires; used to position phase markers.
  const [duration, setDuration] = useState<number>(0);

  const imageSeries: ImageKeypointSeries | null =
    keypoints?.inline?.image ?? null;
  // The keypoint timeseries can be sampled at a different rate than the
  // video (e.g. video=60fps, keypoints decimated to 30fps). The keyboard
  // ±1 frame stepping uses the video fps; overlay frame lookup uses the
  // keypoint fps. They're equal in production today, distinct in spec.
  const kpFps = keypoints?.fps ?? fps;
  const [resW, resH] = resolution;
  const hasOverlay = imageSeries !== null && imageSeries.length > 0;
  const phaseList = orderedPhases(phases);

  const seekToMs = (tMs: number) => {
    const v = videoRef.current;
    if (!v) return;
    v.currentTime = tMs / 1000;
  };

  // Imperative handle for the compare view to drive playback from outside.
  // Empty deps array — videoRef is a ref, the closures capture .current
  // each call.
  useImperativeHandle(
    ref,
    () => ({
      play: () => {
        // Promise rejection fires on autoplay-blocked / user-gesture
        // requirements; the parent recovers via the user clicking again.
        videoRef.current?.play().catch(() => {});
      },
      pause: () => videoRef.current?.pause(),
      seekTo: (s: number) => {
        const v = videoRef.current;
        if (!v) return;
        v.currentTime = s;
      },
      getCurrentTime: () => videoRef.current?.currentTime ?? 0,
      isPaused: () => videoRef.current?.paused ?? true,
    }),
    [],
  );

  // Cross-player callbacks. Separate effect so the existing scrubber +
  // overlay effects don't grow conditionals.
  useEffect(() => {
    if (!videoUrl) return;
    if (!onPlay && !onPause && !onSeek) return;
    const v = videoRef.current;
    if (!v) return;

    const handlePlay = () => onPlay?.();
    const handlePause = () => onPause?.();
    const handleSeeked = () => onSeek?.(v.currentTime);

    v.addEventListener("play", handlePlay);
    v.addEventListener("pause", handlePause);
    v.addEventListener("seeked", handleSeeked);
    return () => {
      v.removeEventListener("play", handlePlay);
      v.removeEventListener("pause", handlePause);
      v.removeEventListener("seeked", handleSeeked);
    };
  }, [videoUrl, onPlay, onPause, onSeek]);

  // Keyboard navigation: ←/→ step one video frame, ↑/↓ jump phase markers.
  // Listener lives on window so it works regardless of which child is
  // focused, but we bail when an editable surface owns the event so a
  // future text input on this page won't lose its arrow-key cursor moves.
  useEffect(() => {
    if (!videoUrl) return;
    if (disableKeyboard) return;
    const frameStep = 1 / Math.max(fps, 1);

    const isEditable = (target: EventTarget | null): boolean => {
      if (!(target instanceof HTMLElement)) return false;
      const tag = target.tagName;
      if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return true;
      return target.isContentEditable;
    };

    const phaseTimes = phaseList.map(([, p]) => p.tMs / 1000);

    const onKey = (e: KeyboardEvent) => {
      if (e.altKey || e.ctrlKey || e.metaKey) return;
      if (isEditable(e.target)) return;
      const v = videoRef.current;
      if (!v) return;

      switch (e.key) {
        case "ArrowLeft": {
          e.preventDefault();
          if (!v.paused) v.pause();
          v.currentTime = Math.max(0, v.currentTime - frameStep);
          break;
        }
        case "ArrowRight": {
          e.preventDefault();
          if (!v.paused) v.pause();
          // Don't read v.duration — it's NaN until loadedmetadata.
          // Browser clamps overshoot anyway.
          v.currentTime = v.currentTime + frameStep;
          break;
        }
        case "ArrowUp": {
          if (phaseTimes.length === 0) return;
          e.preventDefault();
          // Previous phase: last one strictly before currentTime.
          // 10ms epsilon so a click that lands exactly on a marker
          // can still step backward off it.
          const t = v.currentTime - 0.01;
          const prev = [...phaseTimes].reverse().find((pt) => pt < t);
          if (prev !== undefined) v.currentTime = prev;
          break;
        }
        case "ArrowDown": {
          if (phaseTimes.length === 0) return;
          e.preventDefault();
          const t = v.currentTime + 0.01;
          const next = phaseTimes.find((pt) => pt > t);
          if (next !== undefined) v.currentTime = next;
          break;
        }
      }
    };

    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [videoUrl, fps, phaseList, disableKeyboard]);

  // Scrubber playhead. Imperative left% via ref, same reasoning as the
  // SVG: setState 60×/sec for a single style attribute is wasteful. Lives
  // in its own effect so the scrubber works even when there are no
  // keypoints (the SVG-overlay effect early-returns in that case).
  useEffect(() => {
    if (!videoUrl) return;
    const video = videoRef.current;
    if (!video) return;
    let raf: number | null = null;

    const updatePlayhead = () => {
      const head = playheadRef.current;
      const d = video.duration;
      if (!head || !Number.isFinite(d) || d <= 0) return;
      const pct = Math.max(0, Math.min(100, (video.currentTime / d) * 100));
      head.style.left = `${pct}%`;
    };

    const onLoadedMeta = () => {
      if (Number.isFinite(video.duration)) setDuration(video.duration);
      updatePlayhead();
    };
    const loop = () => {
      updatePlayhead();
      raf = requestAnimationFrame(loop);
    };
    const onPlay = () => {
      if (raf === null) raf = requestAnimationFrame(loop);
    };
    const onStop = () => {
      if (raf !== null) {
        cancelAnimationFrame(raf);
        raf = null;
      }
      updatePlayhead();
    };

    video.addEventListener("loadedmetadata", onLoadedMeta);
    video.addEventListener("play", onPlay);
    video.addEventListener("pause", onStop);
    video.addEventListener("seeked", updatePlayhead);
    video.addEventListener("ended", onStop);

    // Browser may have already loaded metadata before this effect ran.
    if (Number.isFinite(video.duration) && video.duration > 0) onLoadedMeta();

    return () => {
      if (raf !== null) cancelAnimationFrame(raf);
      video.removeEventListener("loadedmetadata", onLoadedMeta);
      video.removeEventListener("play", onPlay);
      video.removeEventListener("pause", onStop);
      video.removeEventListener("seeked", updatePlayhead);
      video.removeEventListener("ended", onStop);
    };
  }, [videoUrl]);

  const handleScrubClick = (e: React.MouseEvent<HTMLDivElement>) => {
    const v = videoRef.current;
    const bar = scrubberRef.current;
    if (!v || !bar) return;
    if (!Number.isFinite(v.duration) || v.duration <= 0) return;
    const rect = bar.getBoundingClientRect();
    const fraction = Math.max(
      0,
      Math.min(1, (e.clientX - rect.left) / rect.width),
    );
    v.currentTime = fraction * v.duration;
  };

  useEffect(() => {
    if (!hasOverlay || !imageSeries) return;
    const video = videoRef.current;
    const svg = svgRef.current;
    if (!video || !svg) return;

    const totalFrames = imageSeries.length;

    const drawOnce = () => {
      const t = video.currentTime;
      const idx = Math.min(
        totalFrames - 1,
        Math.max(0, Math.floor(t * kpFps)),
      );
      drawFrame(svg, imageSeries[idx], resW, resH);
    };

    const loop = () => {
      drawOnce();
      rafRef.current = requestAnimationFrame(loop);
    };

    const onPlay = () => {
      if (rafRef.current === null) {
        rafRef.current = requestAnimationFrame(loop);
      }
    };
    const onStop = () => {
      if (rafRef.current !== null) {
        cancelAnimationFrame(rafRef.current);
        rafRef.current = null;
      }
      // Repaint the current frame so the paused/sought state still shows
      // the skeleton at that exact frame.
      drawOnce();
    };

    video.addEventListener("play", onPlay);
    video.addEventListener("pause", onStop);
    video.addEventListener("seeked", drawOnce);
    video.addEventListener("loadedmetadata", drawOnce);
    video.addEventListener("ended", onStop);

    drawOnce();

    return () => {
      if (rafRef.current !== null) {
        cancelAnimationFrame(rafRef.current);
        rafRef.current = null;
      }
      video.removeEventListener("play", onPlay);
      video.removeEventListener("pause", onStop);
      video.removeEventListener("seeked", drawOnce);
      video.removeEventListener("loadedmetadata", drawOnce);
      video.removeEventListener("ended", onStop);
    };
  }, [hasOverlay, imageSeries, kpFps, resW, resH]);

  return (
    <>
      <div className="border border-ink-800 bg-ink-900 aspect-video flex items-center justify-center relative overflow-hidden">
        {videoUrl ? (
          <video
            ref={videoRef}
            controls
            src={videoUrl}
            className="w-full h-full object-contain"
          />
        ) : (
          <div className="text-ink-500 font-mono text-xs uppercase tracking-wider2">
            video expired
          </div>
        )}

        {videoUrl && hasOverlay && (
          <svg
            ref={svgRef}
            viewBox={`0 0 ${resW} ${resH}`}
            preserveAspectRatio="xMidYMid meet"
            className="absolute inset-0 w-full h-full pointer-events-none"
            aria-hidden="true"
          />
        )}

        <div className="absolute top-3 left-3 flex flex-wrap items-center gap-1.5">
          <div className="font-mono text-[10px] uppercase tracking-wider2 text-ink-300 bg-ink-950/80 px-2 py-1 border border-ink-700">
            {view} / {club}
          </div>
          {motionScore !== undefined && (
            <div
              className={`font-mono text-[10px] uppercase tracking-wider2 bg-ink-950/80 px-2 py-1 border ${
                status === "rejected"
                  ? "border-signal-red/60 text-signal-red"
                  : "border-accent/50 text-accent"
              }`}
            >
              motion {motionScore.toFixed(1)} m/s
            </div>
          )}
        </div>

        {videoUrl && !hasOverlay && keypoints?.storageRef && (
          <div className="absolute bottom-3 right-3 font-mono text-[10px] uppercase tracking-wider2 text-ink-400 bg-ink-950/80 px-2 py-1 border border-ink-700">
            keypoints offloaded · overlay deferred to v1.5
          </div>
        )}
      </div>

      {(videoUrl || phaseList.length > 0) && (
        <div className="border border-ink-800 px-4 pt-3 pb-4">
          <div className="flex items-baseline justify-between mb-3">
            <div className="font-mono text-[10px] uppercase tracking-wider2 text-ink-400">
              timeline
            </div>
            <div className="font-mono text-[10px] text-ink-500 num">
              {duration > 0 ? `${duration.toFixed(2)}s` : "—"}
            </div>
          </div>

          {videoUrl && (
            <div className="relative">
              {/* Phase labels float above their dot positions on the
                  track. Click jumps; replaces the old separate pill
                  grid below the bar. */}
              {duration > 0 && phaseList.length > 0 && (
                <div className="relative h-4 mb-2">
                  {phaseList.map(([name, p]) => {
                    const pct = Math.max(
                      0,
                      Math.min(100, (p.tMs / 1000 / duration) * 100),
                    );
                    return (
                      <button
                        key={name}
                        type="button"
                        onClick={() => seekToMs(p.tMs)}
                        title={`${name} · ${p.tMs} ms`}
                        className="absolute top-0 -translate-x-1/2 px-1 font-mono text-[9px] uppercase tracking-wider2 text-ink-400 hover:text-accent focus:outline-none focus-visible:text-accent transition-colors whitespace-nowrap"
                        style={{ left: `${pct}%` }}
                      >
                        {name}
                      </button>
                    );
                  })}
                </div>
              )}

              <div
                ref={scrubberRef}
                onClick={handleScrubClick}
                role="slider"
                aria-label="seek video"
                aria-valuemin={0}
                aria-valuemax={duration || 0}
                aria-valuenow={0}
                tabIndex={-1}
                className="relative h-7 bg-ink-900 border border-ink-800 hover:border-ink-700 cursor-pointer transition-colors group"
              >
                {/* Frame tick dots — every 100 ms. Capped so a
                    pathologically long clip doesn't render thousands. */}
                {duration > 0 &&
                  Array.from({
                    length: Math.min(80, Math.floor(duration * 10) + 1),
                  }).map((_, i) => {
                    const tickT = i * 0.1;
                    const pct = (tickT / duration) * 100;
                    return (
                      <div
                        key={i}
                        className="absolute w-[2px] h-[2px] rounded-full bg-ink-700 pointer-events-none"
                        style={{
                          left: `${pct}%`,
                          top: "50%",
                          transform: "translate(-50%, -50%)",
                        }}
                      />
                    );
                  })}

                {/* Phase dots on the track. Larger + glowing accent so
                    they read as the structural markers, not the ticks. */}
                {duration > 0 &&
                  phaseList.map(([name, p]) => {
                    const pct = Math.max(
                      0,
                      Math.min(100, (p.tMs / 1000 / duration) * 100),
                    );
                    return (
                      <div
                        key={name}
                        className="absolute w-1.5 h-1.5 rounded-full bg-accent/70 pointer-events-none shadow-[0_0_4px_1px_theme(colors.accent)]"
                        style={{
                          left: `${pct}%`,
                          top: "50%",
                          transform: "translate(-50%, -50%)",
                        }}
                        aria-hidden="true"
                      />
                    );
                  })}

                {/* Playhead spans full track height; brighter than the
                    phase dots so it reads as "you are here". */}
                <div
                  ref={playheadRef}
                  className="absolute top-0 bottom-0 w-0.5 bg-accent pointer-events-none shadow-[0_0_5px_1px_theme(colors.accent)]"
                  style={{ left: "0%" }}
                  aria-hidden="true"
                />
              </div>

              {/* tMs readouts under the labels — small, mono, only when
                  the user actively wants the numbers. Lives on the
                  scrubber container so labels above stay short. */}
              {duration > 0 && phaseList.length > 0 && (
                <div className="relative h-3 mt-1.5">
                  {phaseList.map(([name, p]) => {
                    const pct = Math.max(
                      0,
                      Math.min(100, (p.tMs / 1000 / duration) * 100),
                    );
                    return (
                      <span
                        key={name}
                        className="absolute top-0 -translate-x-1/2 font-mono text-[9px] text-ink-500 num whitespace-nowrap"
                        style={{ left: `${pct}%` }}
                      >
                        {p.tMs}
                      </span>
                    );
                  })}
                </div>
              )}
            </div>
          )}

          {/* Video-less mode: no track to label, but the user still
              wants to see what phases were detected. */}
          {!videoUrl && phaseList.length > 0 && (
            <div className="grid grid-cols-6 gap-2 font-mono text-xs">
              {phaseList.map(([name, p]) => (
                <div
                  key={name}
                  className="text-center px-2 py-2 border border-ink-800 opacity-60"
                >
                  <div className="text-ink-500">{name}</div>
                  <div className="text-ink-300 num">{p.tMs}ms</div>
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </>
  );
});
