import Link from "next/link";
import { getSwing, type Swing } from "@/lib/api";
import { CompareView } from "@/components/CompareView";

export const dynamic = "force-dynamic";

export default async function ComparePage({
  searchParams,
}: {
  searchParams: Promise<{ a?: string; b?: string }>;
}) {
  const { a, b } = await searchParams;

  if (!a || !b) {
    return (
      <div className="space-y-6">
        <Link
          href="/swings"
          className="font-mono text-xs uppercase tracking-wider2 text-ink-400 hover:text-accent"
        >
          ← swings
        </Link>
        <div className="border border-dashed border-ink-700 px-6 py-12 text-center">
          <div className="font-display text-xl mb-2">Pick two swings</div>
          <div className="text-ink-400 text-sm">
            Open the{" "}
            <Link href="/swings" className="text-accent hover:underline">
              swings list
            </Link>
            , select one, then hit “compare” on a second.
          </div>
          <div className="text-ink-500 text-xs mt-4 font-mono">
            expects /compare?a=&lt;swing_id&gt;&amp;b=&lt;swing_id&gt;
          </div>
        </div>
      </div>
    );
  }

  // Each swing is fetched independently; the API mints two presigned
  // videoUrls (1 h expiry each). A user comparing for >1 h would see one
  // video go dark before the other, with no graceful refresh path. Out
  // of scope for now — fix when the URL refresh / CDN strategy lands.
  let swingA: Swing | null = null;
  let swingB: Swing | null = null;
  let error: string | null = null;
  try {
    [swingA, swingB] = await Promise.all([getSwing(a), getSwing(b)]);
  } catch (e) {
    error = (e as Error).message;
  }

  if (error || !swingA || !swingB) {
    return (
      <div className="space-y-6">
        <Link
          href="/swings"
          className="font-mono text-xs uppercase tracking-wider2 text-ink-400 hover:text-accent"
        >
          ← swings
        </Link>
        <div className="border border-signal-red/40 bg-signal-red/5 px-4 py-3 font-mono text-sm">
          Could not load comparison ({a} vs {b}): {error ?? "swing(s) not found"}
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-8">
      <Link
        href="/swings"
        className="font-mono text-xs uppercase tracking-wider2 text-ink-400 hover:text-accent"
      >
        ← swings
      </Link>

      <section className="flex items-end justify-between gap-6">
        <div>
          <h1 className="font-display text-4xl tracking-tight">Compare</h1>
          <p className="text-ink-400 text-sm mt-1">
            Two swings, linked playback. Play, pause, or seek either side —
            the other follows.
          </p>
        </div>
        <div className="font-mono text-[10px] uppercase tracking-wider2 text-ink-400 text-right">
          <div>a vs b</div>
          <div className="text-ink-500 mt-0.5 normal-case tracking-normal">
            {swingA._id} <span className="text-ink-700">·</span> {swingB._id}
          </div>
        </div>
      </section>

      <CompareView a={swingA} b={swingB} />
    </div>
  );
}
