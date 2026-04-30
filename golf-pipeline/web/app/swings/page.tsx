import { listSwings, type Swing } from "@/lib/api";
import { SwingsList } from "@/components/SwingsList";

export const dynamic = "force-dynamic";

const PAGE_LIMIT = 50;

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

      <SwingsList swings={swings} />
    </div>
  );
}
