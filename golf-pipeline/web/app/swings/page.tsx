import Link from "next/link";
import { API_BASE, listSwings, type Swing } from "@/lib/api";
import { SwingsList } from "@/components/SwingsList";

export const dynamic = "force-dynamic";

const PAGE_LIMIT = 50;

// Canonical order — matches the Club union in lib/api.ts so a 7i pill
// always sits between 6i and 8i regardless of the order we happen to
// see clubs in the data.
const CLUB_ORDER = [
  "driver", "3w", "5w", "hybrid",
  "3i", "4i", "5i", "6i", "7i", "8i", "9i",
  "pw", "gw", "sw", "lw", "putter",
] as const;

export default async function SwingsPage({
  searchParams,
}: {
  searchParams: Promise<{ club?: string; rejected?: string }>;
}) {
  const { club: clubParam, rejected } = await searchParams;
  const showRejected = rejected === "1";

  let allSwings: Swing[] = [];
  let error: string | null = null;
  try {
    const all = await listSwings();
    allSwings = all.slice(0, PAGE_LIMIT);
  } catch (e) {
    error = (e as Error).message;
  }

  const presentClubs = CLUB_ORDER.filter((c) =>
    allSwings.some((s) => s.capture.club === c),
  );

  const activeClub =
    clubParam && presentClubs.includes(clubParam as (typeof CLUB_ORDER)[number])
      ? clubParam
      : null;

  const clubSwings = activeClub
    ? allSwings.filter((s) => s.capture.club === activeClub)
    : allSwings;
  const rejectedSwings = clubSwings.filter((s) => s.status === "rejected");
  const swings = showRejected
    ? clubSwings
    : clubSwings.filter((s) => s.status !== "rejected");

  return (
    <div className="space-y-10">
      <section className="flex items-end justify-between">
        <div>
          <h1 className="font-display text-4xl tracking-tight">
            Swings
            <span className="text-ink-500 ml-3 font-mono text-base align-middle num">
              {swings.length.toString().padStart(3, "0")}
            </span>
            {(activeClub || !showRejected) && (
              <span className="text-ink-600 ml-2 font-mono text-base align-middle num">
                /{clubSwings.length.toString().padStart(3, "0")}
              </span>
            )}
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
            Make sure `uvicorn golf_pipeline.api.server:app` is reachable at {API_BASE}.
          </div>
        </div>
      )}

      {!error && allSwings.length === 0 && (
        <div className="border border-dashed border-ink-700 px-6 py-12 text-center">
          <div className="font-display text-xl mb-2">No swings yet</div>
          <div className="text-ink-400 text-sm">
            Capture a session, finalize it, and individual swings will show up
            here once the segmenter has run.
          </div>
        </div>
      )}

      {!error && allSwings.length > 0 && presentClubs.length > 1 && (
        <ClubFilterBar
          present={presentClubs}
          active={activeClub}
          showRejected={showRejected}
        />
      )}

      {!error && rejectedSwings.length > 0 && (
        <div className="flex items-center justify-between border-t border-b border-ink-800 py-3">
          <div className="font-mono text-[10px] uppercase tracking-wider2 text-ink-500">
            rejected audio detections hidden by default
          </div>
          {showRejected ? (
            <Link
              href={swingsHref(activeClub, false)}
              className="font-mono text-[10px] uppercase tracking-wider2 text-ink-400 hover:text-accent"
            >
              hide rejected
            </Link>
          ) : (
            <Link
              href={swingsHref(activeClub, true)}
              className="font-mono text-[10px] uppercase tracking-wider2 text-signal-amber hover:text-accent"
            >
              show {rejectedSwings.length} rejected
            </Link>
          )}
        </div>
      )}

      {!error && activeClub && swings.length === 0 && (
        <div className="border border-dashed border-ink-700 px-6 py-10 text-center">
          <div className="font-mono text-xs uppercase tracking-wider2 text-ink-400">
            no swings match the {activeClub} filter — try{" "}
            <Link href="/swings" className="text-accent hover:underline">
              all clubs
            </Link>
          </div>
        </div>
      )}

      <SwingsList swings={swings} />
    </div>
  );
}

interface FilterBarProps {
  present: readonly string[];
  active: string | null;
  showRejected: boolean;
}

function ClubFilterBar({ present, active, showRejected }: FilterBarProps) {
  return (
    <nav
      aria-label="Filter swings by club"
      className="flex flex-wrap items-center gap-1.5"
    >
      <FilterPill
        label="all"
        href={swingsHref(null, showRejected)}
        active={active === null}
      />
      {present.map((c) => (
        <FilterPill
          key={c}
          label={c}
          href={swingsHref(c, showRejected)}
          active={active === c}
        />
      ))}
    </nav>
  );
}

function swingsHref(club: string | null, showRejected: boolean) {
  const params = new URLSearchParams();
  if (club) params.set("club", club);
  if (showRejected) params.set("rejected", "1");
  const query = params.toString();
  return query ? `/swings?${query}` : "/swings";
}

function FilterPill({
  label,
  href,
  active,
}: {
  label: string;
  href: string;
  active: boolean;
}) {
  const base =
    "font-mono text-[11px] uppercase tracking-wider2 px-3 py-1.5 border transition-colors focus:outline-none focus-visible:ring-1 focus-visible:ring-accent";
  const cls = active
    ? `${base} border-accent/70 text-accent bg-accent/10`
    : `${base} border-ink-800 text-ink-400 hover:border-ink-600 hover:text-ink-100`;
  // active pill is non-link (clicking it again would be a no-op); anchoring
  // it as a span avoids the focus highlight implying interactivity.
  if (active) return <span className={cls}>{label}</span>;
  return (
    <Link href={href} className={cls}>
      {label}
    </Link>
  );
}
