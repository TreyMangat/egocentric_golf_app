"use client";

import { useState } from "react";
import type { Club } from "@/lib/api";

const COMMON: Club[] = ["driver", "7i", "pw", "sw"];
const ALL: Club[] = [
  "driver", "3w", "5w", "hybrid",
  "3i", "4i", "5i", "6i", "7i", "8i", "9i",
  "pw", "gw", "sw", "lw", "putter",
];

interface Props {
  selected: Club;
  onChange: (c: Club) => void;
}

export function ClubPicker({ selected, onChange }: Props) {
  const [open, setOpen] = useState(false);

  return (
    <div className="flex items-center gap-1.5">
      {COMMON.map((club) => (
        <button
          key={club}
          onClick={() => onChange(club)}
          className={`font-mono text-[11px] font-bold uppercase tracking-wider2 w-14 h-8 rounded-sm transition-colors ${
            selected === club
              ? "bg-accent text-black"
              : "bg-white/10 text-white hover:bg-white/20"
          }`}
        >
          {club}
        </button>
      ))}
      <button
        onClick={() => setOpen(true)}
        className="font-mono text-[11px] font-bold w-10 h-8 rounded-sm bg-white/10 text-white hover:bg-white/20"
      >
        ···
      </button>

      {open && (
        <div
          className="fixed inset-0 bg-black/85 z-50 flex items-end"
          onClick={() => setOpen(false)}
        >
          <div
            className="w-full bg-ink-950 border-t border-ink-700 p-4 pb-8"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="font-mono text-[10px] uppercase tracking-wider2 text-ink-400 mb-3">
              select club
            </div>
            <div className="grid grid-cols-4 gap-2">
              {ALL.map((club) => (
                <button
                  key={club}
                  onClick={() => {
                    onChange(club);
                    setOpen(false);
                  }}
                  className={`font-mono text-sm font-bold uppercase py-3 rounded-sm transition-colors ${
                    selected === club
                      ? "bg-accent text-black"
                      : "bg-white/8 text-white"
                  }`}
                >
                  {club}
                </button>
              ))}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
