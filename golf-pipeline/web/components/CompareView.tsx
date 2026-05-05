"use client";

import Link from "next/link";
import { useRef } from "react";
import type { Swing } from "@/lib/api";
import {
  SwingPlayer,
  type SwingPlayerHandle,
} from "@/components/SwingPlayer";

interface Props {
  a: Swing;
  b: Swing;
}

// Tolerance for "already in sync". Calling seekTo on a video that's already
// at the requested time is cheap, but the resulting "seeked" event would
// bounce back through the sibling and tight-loop. 50 ms is comfortably
// inside one video frame at 30 fps, so a real user-driven seek is never
// mistaken for an echo.
const SEEK_EPSILON_S = 0.05;

function fmtTimestamp(iso: string): string {
  return new Date(iso).toISOString().replace("T", " ").slice(0, 16);
}

export function CompareView({ a, b }: Props) {
  const refA = useRef<SwingPlayerHandle>(null);
  const refB = useRef<SwingPlayerHandle>(null);

  // Mirror handlers — when one player's event fires, drive the other to
  // match. Each direction self-cancels:
  //  - play / pause: calling play() on an already-playing video doesn't
  //    fire another "play" event, so the chain terminates after one hop.
  //  - seek: the epsilon check on getCurrentTime drops the echo.
  const mirror = (other: React.RefObject<SwingPlayerHandle | null>) => ({
    onPlay: () => other.current?.play(),
    onPause: () => other.current?.pause(),
    onSeek: (t: number) => {
      const o = other.current;
      if (!o) return;
      if (Math.abs(o.getCurrentTime() - t) < SEEK_EPSILON_S) return;
      o.seekTo(t);
    },
  });

  return (
    <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
      <Pane swing={a} ref={refA} {...mirror(refB)} />
      <Pane swing={b} ref={refB} {...mirror(refA)} />
    </div>
  );
}

interface PaneProps {
  swing: Swing;
  onPlay: () => void;
  onPause: () => void;
  onSeek: (t: number) => void;
}

const Pane = function PaneInner({
  ref,
  swing,
  onPlay,
  onPause,
  onSeek,
}: PaneProps & { ref: React.Ref<SwingPlayerHandle> }) {
  return (
    <div className="space-y-3">
      <div className="flex items-baseline justify-between">
        <Link
          href={`/swing/${swing._id}`}
          className="font-display text-xl tracking-tight hover:text-accent transition-colors"
        >
          {swing.capture.club}
          <span className="text-ink-400 font-mono text-xs uppercase tracking-wider2 ml-3">
            {swing.capture.view}
          </span>
        </Link>
        <span className="font-mono text-[10px] uppercase tracking-wider2 text-ink-400 num">
          {fmtTimestamp(swing.createdAt)}
        </span>
      </div>
      <SwingPlayer
        ref={ref}
        swingId={swing._id}
        videoUrl={swing.videoUrl}
        resolution={swing.capture.resolution}
        view={swing.capture.view}
        club={swing.capture.club}
        fps={swing.capture.fps}
        keypoints={swing.keypoints}
        phases={swing.phases}
        // Two players + one window keyboard listener would double-handle
        // every arrow press. Disable the listener on both panes here; the
        // compare view is a passive viewer for now (no keyboard nav).
        disableKeyboard
        onPlay={onPlay}
        onPause={onPause}
        onSeek={onSeek}
      />
    </div>
  );
};
