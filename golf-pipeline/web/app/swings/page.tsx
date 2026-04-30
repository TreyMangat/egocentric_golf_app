import Link from "next/link";
import { listSwings, type Swing } from "@/lib/api";

export const dynamic = "force-dynamic";

const PAGE_LIMIT = 50;

function fmtTimestamp(iso: string): string {
  return new Date(iso).toISOString().replace("T", " ").slice(0, 16);
}

export default async function SwingsPage() {
  let swings: Swing[] = [];
  let error: string | null = null;
  try {
    // Backend already sorts by createdAt desc and caps at 50; the slice here
    // is a defensive cap if that limit later widens.
    const all = await listSwings();
    swings = all.slice(0, PAGE_LIMIT);
  } catch (e) {
    error = (e as Error).message;
  }

  return (
    <div className="space-y-10">
      <section className="flex items-end justify-between">
        <div>
          <h1 className="font-display text-4xl tracking-tight">
            Swings
            <span className="text-ink-500 ml-3 font-mono text-base align-middle num">
              {swings.length.toString().padStart(3, "0")}
            </span>
          </h1>
          <p className="text-ink-400 text-sm mt-1">
            All swings across every session, newest first. Showing the most
            recent {PAGE_LIMIT}.
          </p>
        </div>
        <div className="font-mono text-xs uppercase tracking-wider2 text-ink-400">
          {new Date().toISOString().slice(0, 10)}
        </div>
      </section>

      {error && (
        <div className="border border-signal-red/40 bg-signal-red/5 px-4 py-3 font-mono text-sm">
          API unreachable: {error}
          <div className="text-ink-400 mt-1 text-xs">
            Make sure `uvicorn golf_pipeline.api.server:app` is running on port 8000.
          </div>
        </div>
      )}

      {!error && swings.length === 0 && (
        <div className="border border-dashed border-ink-700 px-6 py-12 text-center">
          <div className="font-display text-xl mb-2">No swings yet</div>
          <div className="text-ink-400 text-sm">
            Capture a session, finalize it, and individual swings will show up
            here once the segmenter has run.
          </div>
        </div>
      )}

      <div className="grid gap-2">
        {swings.map((s) => {
          const tempo = s.metrics.tempoRatioBackswingDownswing;
          return (
            <Link
              key={s._id}
              href={`/swing/${s._id}`}
              className="group flex items-center justify-between border border-ink-800 hover:border-accent/60 hover:bg-ink-900 px-5 py-4 transition-colors"
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
          );
        })}
      </div>
    </div>
  );
}
