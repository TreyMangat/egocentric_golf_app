// A compact metric tile with a status bar — the signature visual.

import type { RangeStatus } from "@/lib/api";

interface Props {
  label: string;
  value: number | string | null;
  unit?: string;
  target?: [number, number];
  status?: RangeStatus;
}

const statusColor: Record<RangeStatus, string> = {
  pass: "bg-signal-green",
  warn: "bg-signal-amber",
  fail: "bg-signal-red",
};

export function MetricBadge({ label, value, unit, target, status }: Props) {
  const hasValue = value !== null && value !== undefined && value !== "";
  return (
    <div className="border border-ink-800 bg-ink-900/60 px-4 py-3 group hover:border-ink-700 transition-colors">
      <div className="flex items-baseline justify-between mb-2">
        <div className="font-mono text-[10px] tracking-wider2 uppercase text-ink-400">
          {label}
        </div>
        {status && (
          <div
            className={`w-1.5 h-1.5 rounded-full ${statusColor[status]} ${
              status === "pass" ? "shadow-[0_0_6px_1px_theme(colors.signal.green)]" : ""
            }`}
            aria-label={status}
          />
        )}
      </div>
      <div className="flex items-baseline gap-1.5">
        <span className="font-mono text-2xl num text-ink-100">
          {hasValue ? value : "—"}
        </span>
        {unit && hasValue && (
          <span className="font-mono text-xs text-ink-400">{unit}</span>
        )}
      </div>
      {target && (
        <div className="font-mono text-[10px] text-ink-500 mt-1.5 num">
          target {target[0]}–{target[1]}
        </div>
      )}
    </div>
  );
}
