"use client";

import { useState } from "react";
import type { Outcome, Shape } from "@/lib/api";

const OUTCOMES: Outcome[] = ["good", "ok", "bad"];
const SHAPES: Shape[] = ["straight", "draw", "fade", "hook", "slice", "fat", "thin"];

const OUTCOME_TINT: Record<Outcome, string> = {
  good: "bg-signal-green text-black",
  ok: "bg-signal-amber text-black",
  bad: "bg-signal-red text-black",
};

interface Props {
  tagCount: number;
  onTag: (outcome: Outcome | null, shape: Shape | null) => void;
}

export function TagPanel({ tagCount, onTag }: Props) {
  const [outcome, setOutcome] = useState<Outcome | null>(null);
  const [shape, setShape] = useState<Shape | null>(null);
  const canSave = outcome !== null || shape !== null;

  const reset = () => {
    setOutcome(null);
    setShape(null);
  };

  return (
    <div className="bg-black/65 backdrop-blur-sm border border-white/10 rounded-md p-3 space-y-2.5">
      <div className="flex items-center justify-between">
        <span className="font-mono text-[10px] tracking-wider2 uppercase text-white/50">
          tag last swing
        </span>
        <span className="font-mono text-[10px] text-white/40 num">
          {tagCount} tag{tagCount === 1 ? "" : "s"}
        </span>
      </div>

      <div className="flex gap-1.5">
        {OUTCOMES.map((o) => (
          <button
            key={o}
            onClick={() => setOutcome(outcome === o ? null : o)}
            className={`font-mono text-[10px] font-bold uppercase tracking-wider2 px-2 h-7 rounded-sm flex-1 ${
              outcome === o ? OUTCOME_TINT[o] : "bg-white/8 text-white"
            }`}
          >
            {o}
          </button>
        ))}
      </div>

      <div className="grid grid-cols-4 gap-1.5">
        {SHAPES.map((s) => (
          <button
            key={s}
            onClick={() => setShape(shape === s ? null : s)}
            className={`font-mono text-[10px] font-bold tracking-wide px-1 h-7 rounded-sm ${
              shape === s ? "bg-white text-black" : "bg-white/8 text-white"
            }`}
          >
            {s}
          </button>
        ))}
      </div>

      <button
        disabled={!canSave}
        onClick={() => {
          onTag(outcome, shape);
          reset();
        }}
        className={`w-full h-9 rounded-sm font-mono text-xs font-bold uppercase tracking-wider2 ${
          canSave ? "bg-accent text-black" : "bg-white/10 text-white/30"
        }`}
      >
        save tag
      </button>
    </div>
  );
}
