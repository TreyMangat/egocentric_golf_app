"use client";

import { useEffect, useRef } from "react";
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
  keypoints: KeypointsRef | null | undefined;
  phases: Phases | null | undefined;
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

export function SwingPlayer({
  videoUrl,
  resolution,
  view,
  club,
  keypoints,
  phases,
}: Props) {
  const videoRef = useRef<HTMLVideoElement>(null);
  const svgRef = useRef<SVGSVGElement>(null);
  const rafRef = useRef<number | null>(null);

  const imageSeries: ImageKeypointSeries | null =
    keypoints?.inline?.image ?? null;
  const fps = keypoints?.fps ?? 60;
  const [resW, resH] = resolution;
  const hasOverlay = imageSeries !== null && imageSeries.length > 0;
  const phaseList = orderedPhases(phases);

  const seekToMs = (tMs: number) => {
    const v = videoRef.current;
    if (!v) return;
    v.currentTime = tMs / 1000;
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
        Math.max(0, Math.floor(t * fps)),
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
  }, [hasOverlay, imageSeries, fps, resW, resH]);

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

        <div className="absolute top-3 left-3 font-mono text-[10px] uppercase tracking-wider2 text-ink-300 bg-ink-950/80 px-2 py-1 border border-ink-700">
          {view} / {club}
        </div>

        {videoUrl && !hasOverlay && keypoints?.storageRef && (
          <div className="absolute bottom-3 right-3 font-mono text-[10px] uppercase tracking-wider2 text-ink-400 bg-ink-950/80 px-2 py-1 border border-ink-700">
            keypoints offloaded · overlay deferred to v1.5
          </div>
        )}
      </div>

      {phaseList.length > 0 && (
        <div className="border border-ink-800 px-4 py-3">
          <div className="font-mono text-[10px] uppercase tracking-wider2 text-ink-400 mb-2">
            phases
          </div>
          <div className="grid grid-cols-6 gap-2 font-mono text-xs">
            {phaseList.map(([name, p]) => (
              <button
                key={name}
                type="button"
                onClick={() => seekToMs(p.tMs)}
                disabled={!videoUrl}
                className="text-center px-2 py-2 border border-transparent hover:border-ink-700 hover:bg-ink-900 focus:outline-none focus-visible:border-accent/60 disabled:opacity-40 disabled:cursor-not-allowed transition-colors group"
              >
                <div className="text-ink-500 group-hover:text-ink-300 group-focus-visible:text-ink-200">
                  {name}
                </div>
                <div className="text-ink-100 num">{p.tMs}ms</div>
              </button>
            ))}
          </div>
        </div>
      )}
    </>
  );
}
