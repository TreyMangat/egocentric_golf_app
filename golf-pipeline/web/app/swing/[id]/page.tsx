import Link from "next/link";
import { getSwing, type Swing } from "@/lib/api";
import { MetricBadge } from "@/components/MetricBadge";
import { SwingPlayer } from "@/components/SwingPlayer";

export const dynamic = "force-dynamic";

interface MetricDef {
  key: keyof Swing["metrics"];
  label: string;
  unit: string;
}
interface MetricGroup {
  name: string;
  metrics: MetricDef[];
}

// Grouped by what the metric tells you about the swing, not by the order
// they're computed. wrist-hinge sits with rotation (it's a wind-up); lead
// arm angle sits with posture (it's a static body angle).
const METRIC_GROUPS: MetricGroup[] = [
  {
    name: "tempo",
    metrics: [
      { key: "tempoRatioBackswingDownswing", label: "tempo ratio", unit: ":1" },
      { key: "backswingDurationMs", label: "backswing", unit: "ms" },
      { key: "downswingDurationMs", label: "downswing", unit: "ms" },
    ],
  },
  {
    name: "rotation",
    metrics: [
      { key: "shoulderTurnAtTopDeg", label: "shoulder turn", unit: "°" },
      { key: "hipTurnAtTopDeg", label: "hip turn", unit: "°" },
      { key: "xFactorDeg", label: "x-factor", unit: "°" },
      { key: "wristHingeMaxDeg", label: "wrist hinge", unit: "°" },
    ],
  },
  {
    name: "posture",
    metrics: [
      { key: "headSwayMaxMm", label: "head sway", unit: "mm" },
      { key: "headLiftMaxMm", label: "head lift", unit: "mm" },
      { key: "spineTiltAtAddressDeg", label: "spine @ address", unit: "°" },
      { key: "spineTiltAtImpactDeg", label: "spine @ impact", unit: "°" },
      { key: "leadArmAngleAtTopDeg", label: "lead arm @ top", unit: "°" },
    ],
  },
];

export default async function SwingPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  let swing: Swing | null = null;
  let error: string | null = null;
  try {
    swing = await getSwing(id);
  } catch (e) {
    error = (e as Error).message;
  }

  if (error || !swing) {
    return (
      <div className="border border-signal-red/40 bg-signal-red/5 px-4 py-3 font-mono text-sm">
        Could not load swing {id}: {error ?? "not found"}
      </div>
    );
  }

  const metricsMap = swing.metrics as unknown as Record<string, number | null>;
  const rangesMap = swing.ranges;

  return (
    <div className="space-y-10">
      <Link href="/" className="font-mono text-xs uppercase tracking-wider2 text-ink-400 hover:text-accent">
        ← back
      </Link>

      <section className="grid grid-cols-1 lg:grid-cols-[1.6fr_1fr] gap-8">
        <div className="space-y-4">
          <SwingPlayer
            videoUrl={swing.videoUrl}
            resolution={swing.capture.resolution}
            view={swing.capture.view}
            club={swing.capture.club}
            fps={swing.capture.fps}
            keypoints={swing.keypoints}
            phases={swing.phases}
          />
        </div>

        <aside className="space-y-2">
          <div className="font-mono text-[10px] uppercase tracking-wider2 text-ink-400">
            session
          </div>
          <div className="text-sm">
            <Link href={`/sessions/${swing.sessionId}`} className="hover:text-accent font-mono">
              {swing.sessionId}
            </Link>
          </div>
          <div className="font-mono text-xs text-ink-400 num">
            {new Date(swing.createdAt).toISOString().replace("T", " ").slice(0, 19)}
          </div>
          {swing.tags.outcome && (
            <div className="mt-4">
              <div className="font-mono text-[10px] uppercase tracking-wider2 text-ink-400 mb-1">
                outcome
              </div>
              <div className="font-display text-lg">
                {swing.tags.outcome}
                {swing.tags.shape && (
                  <span className="text-ink-400 text-base ml-2">/ {swing.tags.shape}</span>
                )}
              </div>
            </div>
          )}
        </aside>
      </section>

      <section>
        <div className="flex items-baseline justify-between mb-6">
          <h2 className="font-display text-2xl tracking-tight">Metrics</h2>
          <div className="font-mono text-[10px] uppercase tracking-wider2 text-ink-400">
            tier 1 · biomechanical ranges
          </div>
        </div>
        <div className="space-y-7">
          {METRIC_GROUPS.map((g) => (
            <div key={g.name}>
              <h3 className="font-mono text-[10px] uppercase tracking-wider2 text-ink-300 border-t border-ink-800 pt-3 pb-3 flex items-baseline justify-between">
                <span>{g.name}</span>
                <span className="text-ink-500 normal-case tracking-normal text-[10px] font-mono">
                  {g.metrics.length} metric{g.metrics.length === 1 ? "" : "s"}
                </span>
              </h3>
              <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-3">
                {g.metrics.map(({ key, label, unit }) => {
                  const v = metricsMap[key];
                  const r = rangesMap[key as string];
                  return (
                    <MetricBadge
                      key={key as string}
                      label={label}
                      value={v === null || v === undefined ? null : v}
                      unit={unit}
                      target={r?.target}
                      status={r?.status}
                    />
                  );
                })}
              </div>
            </div>
          ))}
        </div>
      </section>
    </div>
  );
}
