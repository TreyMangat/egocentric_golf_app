import Link from "next/link";
import { listSessions, type Session } from "@/lib/api";

export const dynamic = "force-dynamic";

export default async function HomePage() {
  let sessions: Session[] = [];
  let error: string | null = null;
  try {
    sessions = await listSessions();
  } catch (e) {
    error = (e as Error).message;
  }

  return (
    <div className="space-y-10">
      <section className="flex items-end justify-between">
        <div>
          <h1 className="font-display text-4xl tracking-tight">
            Sessions
            <span className="text-ink-500 ml-3 font-mono text-base align-middle num">
              {sessions.length.toString().padStart(3, "0")}
            </span>
          </h1>
          <p className="text-ink-400 text-sm mt-1">
            Practice sessions captured at the range. Tap one to see swings and
            metric trends.
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

      {!error && sessions.length === 0 && (
        <div className="border border-dashed border-ink-700 px-6 py-12 text-center">
          <div className="font-display text-xl mb-2">No sessions yet</div>
          <div className="text-ink-400 text-sm">
            Capture one with the iOS app, finalize it, and it&apos;ll show up here.
          </div>
        </div>
      )}

      <div className="grid gap-2">
        {sessions.map((s) => (
          <Link
            key={s._id}
            href={`/sessions/${s._id}`}
            className="group flex items-center justify-between border border-ink-800 hover:border-accent/60 hover:bg-ink-900 px-5 py-4 transition-colors"
          >
            <div className="flex items-baseline gap-5">
              <span className="font-mono text-xs text-ink-500 tracking-wider2 uppercase num">
                {new Date(s.startedAt).toISOString().slice(0, 16).replace("T", " ")}
              </span>
              <span className="font-display text-lg group-hover:text-accent transition-colors">
                {s.notes ?? s.location ?? "Range session"}
              </span>
            </div>
            <div className="flex items-center gap-6">
              <div className="font-mono text-xs text-ink-400 num">
                {s.swingCount} swings
              </div>
              {s.summaryMetrics?.tempoRatioMean !== undefined && (
                <div className="font-mono text-xs text-ink-300 num">
                  tempo&nbsp;
                  <span className="text-ink-100">
                    {s.summaryMetrics.tempoRatioMean.toFixed(2)}
                  </span>
                </div>
              )}
              <span className="text-ink-600 group-hover:text-accent">→</span>
            </div>
          </Link>
        ))}
      </div>
    </div>
  );
}
