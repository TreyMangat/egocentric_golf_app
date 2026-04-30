import Link from "next/link";
import { getSession, type Session, type Swing } from "@/lib/api";
import { MetricBadge } from "@/components/MetricBadge";

export const dynamic = "force-dynamic";

const SUMMARY_DEFS: Array<{
  key: string;
  label: string;
  unit: string;
  precision: number;
}> = [
  { key: "tempoRatioMean", label: "tempo mean", unit: ":1", precision: 2 },
  { key: "tempoRatioStd", label: "tempo σ", unit: "", precision: 2 },
  { key: "headSwayMeanMm", label: "head sway mean", unit: "mm", precision: 0 },
];

function fmtTimestamp(iso: string): string {
  return new Date(iso).toISOString().replace("T", " ").slice(0, 16);
}

const TARGET_LO = 2.8;
const TARGET_HI = 3.2;

function TempoTrend({ swings }: { swings: Swing[] }) {
  const sorted = [...swings].sort(
    (a, b) =>
      new Date(a.createdAt).getTime() - new Date(b.createdAt).getTime(),
  );
  const tempos = sorted.map((s) => s.metrics.tempoRatioBackswingDownswing);
  const valid = tempos.filter((t): t is number => t != null);
  if (valid.length === 0) return null;

  // Domain: pad to include the target band so it always reads as in-frame,
  // and pad to data extents so even outliers fit. Round to 0.1 ticks.
  const dataMin = Math.min(...valid);
  const dataMax = Math.max(...valid);
  const yMin = Math.floor(Math.min(dataMin, TARGET_LO - 0.2) * 10) / 10;
  const yMax = Math.ceil(Math.max(dataMax, TARGET_HI + 0.2) * 10) / 10;
  const domain = yMax - yMin;

  // Chart geometry. preserveAspectRatio="none" lets bars widen with the
  // container; using a fixed viewBox keeps all the math simple.
  const W = 800;
  const H = 96;
  const PAD_X = 4;
  const PAD_TOP = 6;
  const PAD_BOT = 6;
  const innerW = W - PAD_X * 2;
  const innerH = H - PAD_TOP - PAD_BOT;
  const slotW = innerW / sorted.length;
  const gap = Math.max(1, Math.min(2.5, slotW * 0.22));
  const barW = Math.max(1, slotW - gap);

  const yFor = (v: number) =>
    PAD_TOP + (1 - (v - yMin) / domain) * innerH;

  const bandTop = yFor(TARGET_HI);
  const bandHeight = yFor(TARGET_LO) - bandTop;

  return (
    <div className="mb-4 border border-ink-800 bg-ink-900/30 px-4 py-3">
      <div className="flex items-baseline justify-between mb-2">
        <div className="font-mono text-[10px] uppercase tracking-wider2 text-ink-400">
          tempo ratio · trend
        </div>
        <div className="font-mono text-[10px] uppercase tracking-wider2 text-ink-500 num">
          y {yMin.toFixed(1)}–{yMax.toFixed(1)} · target {TARGET_LO}–{TARGET_HI}
        </div>
      </div>
      <svg
        viewBox={`0 0 ${W} ${H}`}
        className="w-full h-24 overflow-visible"
        preserveAspectRatio="none"
        role="img"
        aria-label="Tempo ratio per swing across the session"
      >
        {/* Scoped hover styles inside the SVG; arbitrary Tailwind on
            SVG <a> hover is brittle across browsers. */}
        <style>{`
          .bar { fill: #a4adbe; transition: fill 120ms ease-out; }
          a:hover .bar, a:focus .bar { fill: #d4ff5a; }
          a { outline: none; }
        `}</style>

        {/* target band */}
        <rect
          x={PAD_X}
          y={bandTop}
          width={innerW}
          height={bandHeight}
          fill="#d4ff5a"
          fillOpacity="0.06"
        />
        {/* center reference at 3.0 */}
        <line
          x1={PAD_X}
          y1={yFor(3.0)}
          x2={PAD_X + innerW}
          y2={yFor(3.0)}
          stroke="#d4ff5a"
          strokeOpacity="0.25"
          strokeWidth="1"
          strokeDasharray="2 4"
          vectorEffect="non-scaling-stroke"
        />

        {sorted.map((s, i) => {
          const x = PAD_X + i * slotW + gap / 2;
          const t = s.metrics.tempoRatioBackswingDownswing;
          if (t == null) {
            // missing-data ghost: 2px floor strip so the slot isn't empty
            const y = PAD_TOP + innerH - 2;
            return (
              <a key={s._id} href={`/swing/${s._id}`}>
                <rect
                  x={x}
                  y={y}
                  width={barW}
                  height={2}
                  fill="#475160"
                />
                <title>{`#${i + 1} · ${s.capture.club} · no tempo`}</title>
              </a>
            );
          }
          const y = yFor(t);
          const h = Math.max(1, PAD_TOP + innerH - y);
          return (
            <a key={s._id} href={`/swing/${s._id}`}>
              <rect className="bar" x={x} y={y} width={barW} height={h} />
              <title>
                {`#${i + 1} · ${s.capture.club} · ${t.toFixed(2)}:1`}
              </title>
            </a>
          );
        })}
      </svg>
      <div className="mt-1 flex items-center justify-between font-mono text-[10px] uppercase tracking-wider2 text-ink-500 num">
        <span>swing 1</span>
        <span>swing {sorted.length}</span>
      </div>
    </div>
  );
}

export default async function SessionPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  let session: Session | null = null;
  let swings: Swing[] = [];
  let error: string | null = null;
  try {
    const data = await getSession(id);
    session = data.session;
    swings = data.swings;
  } catch (e) {
    error = (e as Error).message;
  }

  if (error || !session) {
    return (
      <div className="border border-signal-red/40 bg-signal-red/5 px-4 py-3 font-mono text-sm">
        Could not load session {id}: {error ?? "not found"}
      </div>
    );
  }

  const hasSummary =
    session.summaryMetrics &&
    Object.keys(session.summaryMetrics).length > 0 &&
    SUMMARY_DEFS.some((d) => session!.summaryMetrics[d.key] !== undefined);

  const title = session.notes ?? session.location ?? "Range session";

  return (
    <div className="space-y-10">
      <Link
        href="/"
        className="font-mono text-xs uppercase tracking-wider2 text-ink-400 hover:text-accent"
      >
        ← sessions
      </Link>

      <section className="flex items-end justify-between gap-6">
        <div>
          <h1 className="font-display text-4xl tracking-tight">
            {title}
            <span className="text-ink-500 ml-3 font-mono text-base align-middle num">
              {session.swingCount.toString().padStart(3, "0")}
            </span>
          </h1>
          <div className="mt-1 flex items-baseline gap-4 font-mono text-xs text-ink-400 num">
            <span className="uppercase tracking-wider2">
              {fmtTimestamp(session.startedAt)}
            </span>
            {session.location && (
              <span className="text-ink-300">{session.location}</span>
            )}
          </div>
          {session.notes && session.notes !== title && (
            <p className="text-ink-300 text-sm mt-3 max-w-prose">
              {session.notes}
            </p>
          )}
        </div>
        <div className="font-mono text-[10px] uppercase tracking-wider2 text-ink-400 text-right">
          <div>session</div>
          <div className="text-ink-500 mt-0.5 normal-case tracking-normal">
            {session._id}
          </div>
        </div>
      </section>

      {hasSummary && (
        <section>
          <div className="flex items-baseline justify-between mb-4">
            <h2 className="font-display text-2xl tracking-tight">Summary</h2>
            <div className="font-mono text-[10px] uppercase tracking-wider2 text-ink-400">
              session aggregates · {session.swingCount} swings
            </div>
          </div>
          <div className="grid grid-cols-2 md:grid-cols-3 gap-3">
            {SUMMARY_DEFS.map(({ key, label, unit, precision }) => {
              const v = session!.summaryMetrics[key];
              return (
                <MetricBadge
                  key={key}
                  label={label}
                  value={v === undefined || v === null ? null : v.toFixed(precision)}
                  unit={unit}
                />
              );
            })}
          </div>
        </section>
      )}

      <section>
        <div className="flex items-baseline justify-between mb-4">
          <h2 className="font-display text-2xl tracking-tight">Swings</h2>
          <div className="font-mono text-[10px] uppercase tracking-wider2 text-ink-400">
            {swings.length} captured
          </div>
        </div>

        {swings.length >= 5 && <TempoTrend swings={swings} />}

        {swings.length === 0 ? (
          <div className="border border-dashed border-ink-700 px-6 py-10 text-center">
            <div className="font-display text-lg mb-1">No swings yet</div>
            <div className="text-ink-400 font-mono text-xs uppercase tracking-wider2">
              segmenter hasn&apos;t produced any windows for this session
            </div>
          </div>
        ) : (
          <div className="grid gap-2">
            {swings.map((s) => {
              const tempo = s.metrics.tempoRatioBackswingDownswing;
              return (
                <Link
                  key={s._id}
                  href={`/swing/${s._id}`}
                  className="group flex items-center justify-between border border-ink-800 hover:border-accent/60 hover:bg-ink-900 px-5 py-4 transition-colors"
                >
                  <div className="flex items-baseline gap-5">
                    <span className="font-mono text-xs text-ink-500 tracking-wider2 uppercase num">
                      {fmtTimestamp(s.createdAt)}
                    </span>
                    <span className="font-display text-lg group-hover:text-accent transition-colors">
                      {s.capture.club}
                    </span>
                    <span className="font-mono text-[10px] uppercase tracking-wider2 text-ink-400">
                      {s.capture.view}
                    </span>
                  </div>
                  <div className="flex items-center gap-6">
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
              );
            })}
          </div>
        )}
      </section>
    </div>
  );
}
