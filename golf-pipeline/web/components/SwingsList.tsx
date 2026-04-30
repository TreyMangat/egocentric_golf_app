"use client";

import Link from "next/link";
import { useState } from "react";
import type { Swing } from "@/lib/api";

interface Props {
  swings: Swing[];
}

function fmtTimestamp(iso: string): string {
  return new Date(iso).toISOString().replace("T", " ").slice(0, 16);
}

export function SwingsList({ swings }: Props) {
  // selectedId is the "first" swing in a comparison; clicking another row's
  // compare button navigates to /compare?a=selectedId&b=rowId. State lives
  // here (not URL or storage) — once the user navigates, the compare page
  // owns the pair. Survives swings-page tab switching: no.
  const [selectedId, setSelectedId] = useState<string | null>(null);

  return (
    <div className="grid gap-2">
      {swings.map((s) => {
        const tempo = s.metrics.tempoRatioBackswingDownswing;
        const isSelected = selectedId === s._id;
        const otherSelected = selectedId !== null && !isSelected;

        return (
          <div
            key={s._id}
            className={`flex items-stretch border transition-colors ${
              isSelected
                ? "border-accent/60 bg-ink-900"
                : "border-ink-800 hover:border-ink-700"
            }`}
          >
            <Link
              href={`/swing/${s._id}`}
              className="flex-1 flex items-center justify-between px-5 py-4 group hover:bg-ink-900 min-w-0"
            >
              <div className="flex items-baseline gap-5 min-w-0">
                <span className="font-mono text-xs text-ink-500 tracking-wider2 uppercase num shrink-0">
                  {fmtTimestamp(s.createdAt)}
                </span>
                <span className="font-display text-lg group-hover:text-accent transition-colors shrink-0">
                  {s.capture.club}
                </span>
                <span className="font-mono text-[10px] uppercase tracking-wider2 text-ink-400 shrink-0">
                  {s.capture.view}
                </span>
                <span className="font-mono text-[10px] text-ink-500 truncate hidden md:inline normal-case">
                  {s.sessionId}
                </span>
              </div>
              <div className="flex items-center gap-6 shrink-0">
                {s.tags.outcome && (
                  <span className="font-mono text-[10px] uppercase tracking-wider2 text-ink-300">
                    {s.tags.outcome}
                    {s.tags.shape && (
                      <span className="text-ink-500"> / {s.tags.shape}</span>
                    )}
                  </span>
                )}
                {tempo !== null && tempo !== undefined ? (
                  <div className="font-mono text-xs text-ink-300 num">
                    tempo&nbsp;
                    <span className="text-ink-100">{tempo.toFixed(2)}</span>
                  </div>
                ) : (
                  <div className="font-mono text-xs text-ink-600 num">—</div>
                )}
                <span className="text-ink-600 group-hover:text-accent">→</span>
              </div>
            </Link>

            <div className="flex items-center px-3 border-l border-ink-800">
              {isSelected ? (
                <button
                  type="button"
                  onClick={() => setSelectedId(null)}
                  className="font-mono text-[10px] uppercase tracking-wider2 text-accent border border-accent/60 px-2 py-1 hover:bg-accent/10 focus:outline-none focus-visible:ring-1 focus-visible:ring-accent"
                  aria-label="Deselect this swing"
                  title="selected for compare — click to cancel"
                >
                  ● selected
                </button>
              ) : otherSelected ? (
                <Link
                  href={`/compare?a=${selectedId}&b=${s._id}`}
                  className="font-mono text-[10px] uppercase tracking-wider2 text-accent border border-ink-700 hover:border-accent/60 hover:bg-ink-800 px-2 py-1"
                  aria-label={`Compare against ${selectedId}`}
                  title={`compare against ${selectedId}`}
                >
                  compare →
                </Link>
              ) : (
                <button
                  type="button"
                  onClick={() => setSelectedId(s._id)}
                  className="font-mono text-[10px] uppercase tracking-wider2 text-ink-400 hover:text-ink-100 border border-transparent hover:border-ink-700 px-2 py-1 focus:outline-none focus-visible:border-ink-700"
                  aria-label="Select this swing for compare"
                  title="select for compare"
                >
                  + compare
                </button>
              )}
            </div>
          </div>
        );
      })}
    </div>
  );
}
