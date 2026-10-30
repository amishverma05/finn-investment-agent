"use client";

import Link from "next/link";
import { useEffect, useMemo, useState, useSyncExternalStore } from "react";
import { ChevronDown, Sparkles } from "lucide-react";
import { readLS, useApi, useStored, writeLS, type Digest, type Holding } from "@/lib/api";
import { useScrollMemory } from "@/lib/nav";
import { pct, tokens, tone } from "@/lib/format";
import { cn } from "@/lib/utils";
import { FilingCard } from "@/components/filing-card";
import { Sparkline } from "@/components/sparkline";
import { Chip, ErrorNote, Header, SectionTitle, Skeleton } from "@/components/ui-bits";

const WINDOWS = [
  { hours: 24, label: "24h" },
  { hours: 72, label: "3 days" },
  { hours: 168, label: "1 week" },
  { hours: 720, label: "30 days" },
];

/** Today's date, read on the client only (the page is prerendered once at build time). */
function useToday() {
  return useSyncExternalStore(
    () => () => {},
    () => new Date().toLocaleDateString("en-IN", { weekday: "short", day: "numeric", month: "short" }),
    () => "",
  );
}

export default function BriefPage() {
  const storedWindow = useStored("finn.window");
  const hours = Number(storedWindow) || 24;
  const setHours = (h: number) => writeLS("finn.window", String(h));
  const [showRoutine, setShowRoutine] = useState(false);
  // When you last opened the Brief; filings newer than this get a dot. Cards only render
  // after data loads on the client, so reading localStorage here can't cause a hydration mismatch.
  const [lastSeen] = useState<number>(() => (typeof window === "undefined" ? 0 : Number(readLS("finn.lastSeen")) || 0));

  useEffect(() => {
    const t = setTimeout(() => {
      try {
        localStorage.setItem("finn.lastSeen", String(Date.now()));
      } catch {}
    }, 4000);
    return () => clearTimeout(t);
  }, []);

  const digest = useApi<Digest>(`/digest?hours=${hours}`);
  const book = useApi<{ items: Holding[] }>("/portfolio");
  useScrollMemory(!!digest.data && !!book.data);

  const groups = useMemo(() => {
    const items = digest.data?.items ?? [];
    return {
      attention: items.filter((i) => i.tier === "attention"),
      notable: items.filter((i) => i.tier === "notable"),
      routine: items.filter((i) => i.tier === "routine"),
    };
  }, [digest.data]);

  const movers = useMemo(
    () =>
      (book.data?.items ?? [])
        .filter((h) => h.market)
        .sort((a, b) => Math.abs(b.market!.change_pct) - Math.abs(a.market!.change_pct)),
    [book.data],
  );

  const today = useToday();
  const isNew = (t: string) => lastSeen > 0 && new Date(t).getTime() > lastSeen;

  return (
    <main>
      <Header eyebrow={today} title="Brief" />

      {/* Lede: the whole screen in one sentence */}
      <section className="px-4">
        {digest.loading && !digest.data ? (
          <Skeleton className="h-16" />
        ) : digest.data ? (
          <p className="font-serif text-[22px] leading-[1.25] text-foreground/95">
            {groups.attention.length === 0 ? (
              <>Nothing urgent across your {book.data?.items.length ?? ""} holdings. </>
            ) : (
              <>
                <span className="text-signal">
                  {groups.attention.length} {groups.attention.length === 1 ? "thing needs" : "things need"} you
                </span>
                {groups.attention.length <= 3 && (
                  <> — {[...new Set(groups.attention.map((a) => a.symbol))].join(", ")}</>
                )}
                .{" "}
              </>
            )}
            <span className="text-muted-foreground">
              {groups.notable.length} worth knowing, {groups.routine.length} routine filings tucked away.
            </span>
          </p>
        ) : null}
      </section>

      <div className="no-scrollbar mt-4 flex gap-2 overflow-x-auto px-4">
        {WINDOWS.map((w) => (
          <Chip
            key={w.hours}
            active={hours === w.hours}
            onClick={() => setHours(w.hours)}
          >
            {w.label}
          </Chip>
        ))}
      </div>

      {/* Pulse: biggest movers first */}
      <SectionTitle right={<span className="text-[11px] text-faint">
            {movers[0]?.market
              ? `${new Date(movers[0].market.date).toLocaleDateString("en-IN", { day: "numeric", month: "short" })} close`
              : ""}
          </span>}>
        Pulse
      </SectionTitle>
      <div className="no-scrollbar flex gap-2 overflow-x-auto px-4 pb-1">
        {book.loading && !book.data
          ? Array.from({ length: 4 }).map((_, i) => <Skeleton key={i} className="h-[84px] w-[118px] shrink-0" />)
          : movers.map((h) => {
              const m = h.market!;
              const hot = (m.volume_ratio ?? 0) >= 2;
              return (
                <Link
                  key={h.symbol}
                  href={`/research/${h.symbol}`}
                  className="w-[118px] shrink-0 rounded-xl border border-border bg-card p-2.5 active:scale-[0.98]"
                >
                  <div className="flex items-center justify-between">
                    <span className="num truncate text-[11px] font-medium">{h.symbol}</span>
                    {hot && <span className="num rounded bg-signal/15 px-1 text-[9px] text-signal">{m.volume_ratio}× vol</span>}
                  </div>
                  <p className={cn("num mt-1 text-[15px] font-medium", tone(m.change_pct))}>{pct(m.change_pct, 2)}</p>
                  <Sparkline data={m.spark} width={96} height={22} className="mt-1" />
                </Link>
              );
            })}
      </div>

      {digest.error && (
        <div className="mt-6">
          <ErrorNote message={`Couldn't load filings: ${digest.error}`} onRetry={digest.reload} />
        </div>
      )}

      {digest.loading && !digest.data && (
        <div className="mt-6 space-y-2.5 px-3">
          {Array.from({ length: 3 }).map((_, i) => (
            <Skeleton key={i} className="h-28" />
          ))}
        </div>
      )}

      {digest.data && (
        <div className={cn("transition-opacity", digest.loading && "opacity-50")}>
          <SectionTitle count={groups.attention.length}>Needs attention</SectionTitle>
          {groups.attention.length === 0 ? (
            <p className="px-4 text-sm text-muted-foreground">Nothing material in this window.</p>
          ) : (
            groups.attention.map((f) => <FilingCard key={f.id} f={f} isNew={isNew(f.time)} />)
          )}

          {groups.notable.length > 0 && (
            <>
              <SectionTitle count={groups.notable.length}>Worth knowing</SectionTitle>
              <div className="mx-3 divide-y divide-border overflow-hidden rounded-2xl border border-border bg-card">
                {groups.notable.map((f) => (
                  <FilingCard key={f.id} f={f} compact isNew={isNew(f.time)} />
                ))}
              </div>
            </>
          )}

          {groups.routine.length > 0 && (
            <div className="mx-3 mt-4">
              <button
                onClick={() => setShowRoutine(!showRoutine)}
                className="flex w-full items-center justify-between rounded-2xl border border-dashed border-border px-4 py-3 text-sm text-muted-foreground"
              >
                <span>
                  {groups.routine.length} routine filings
                  <span className="ml-1.5 text-faint">
                    {[...new Set(groups.routine.map((r) => r.category_label))].slice(0, 3).join(", ").toLowerCase()}
                  </span>
                </span>
                <ChevronDown className={cn("size-4 transition-transform", showRoutine && "rotate-180")} />
              </button>
              {showRoutine && (
                <div className="mt-2 divide-y divide-border overflow-hidden rounded-2xl border border-border bg-card">
                  {groups.routine.map((f) => (
                    <FilingCard key={f.id} f={f} compact />
                  ))}
                </div>
              )}
            </div>
          )}

          <p className="mt-6 flex items-center justify-center gap-1.5 px-4 text-center text-[10px] text-faint">
            <Sparkles className="size-3" />
            {digest.data.ai
              ? digest.data.usage.calls > 0
                ? `Ranked by rules, refined by AI · ${digest.data.usage.calls} call · ${tokens(digest.data.usage.input_tokens + digest.data.usage.output_tokens)} tokens`
                : "Ranked by rules, refined by AI · served from cache, 0 tokens"
              : "Ranked by rules · add a Gemini key for AI summaries"}
          </p>
        </div>
      )}
    </main>
  );
}
