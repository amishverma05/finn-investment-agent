"use client";

import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { canGoBack } from "@/lib/nav";
import { openChat } from "@/lib/chat";
import { Cite } from "@/components/cite";
import { Suspense, useCallback, useEffect, useRef, useState } from "react";
import { ArrowLeft, ArrowUpRight, Check, Loader2, MessageCircle, RefreshCw, X } from "lucide-react";
import {
  api,
  readLS,
  useApi,
  writeLS,
  type Analysis,
  type Flag,
  type Numbers,
  type ResearchJob,
  type ResearchResult,
} from "@/lib/api";
import { cr, pct, price, tokens, tone } from "@/lib/format";
import { cn } from "@/lib/utils";
import { Bars } from "@/components/sparkline";
import { Card, ErrorNote, Skeleton } from "@/components/ui-bits";

const SECTIONS = [
  ["snapshot", "Snapshot"],
  ["promises", "Promised vs delivered"],
  ["story", "Story vs numbers"],
  ["trajectory", "Trajectory"],
  ["balance", "Balance sheet"],
  ["cash", "Cash"],
  ["case", "Bull / Bear"],
] as const;

export default function CompanyPage() {
  return (
    <Suspense
      fallback={
        <div className="space-y-3 p-4">
          <Skeleton className="h-12" />
          <Skeleton className="h-40" />
        </div>
      }
    >
      <Company />
    </Suspense>
  );
}

function Company() {
  const { symbol: raw } = useParams<{ symbol: string }>();
  const router = useRouter();
  const symbol = decodeURIComponent(raw).toUpperCase();
  const nums = useApi<Numbers>(`/research/${symbol}/numbers`);
  const [job, setJob] = useState<ResearchJob | null>(null);
  const poll = useRef<ReturnType<typeof setInterval> | null>(null);
  const loaded = nums.data?.symbol;

  const startAnalysis = useCallback(
    (refresh = false) => {
      if (poll.current) clearInterval(poll.current);
      return api<ResearchJob>(`/research/${symbol}/analysis${refresh ? "?refresh=true" : ""}`, { method: "POST" })
        .then((j) => {
          setJob(j);
          if (j.status !== "running") return;
          poll.current = setInterval(() => {
            api<ResearchJob>(`/research/${symbol}/analysis`)
              .then((s) => {
                setJob(s);
                if (s.status !== "running" && poll.current) clearInterval(poll.current);
              })
              .catch(() => {});
          }, 2000);
        })
        .catch((e) => setJob({ status: "error", error: e instanceof Error ? e.message : "Failed" }));
    },
    [symbol],
  );

  // Numbers arrive first; then kick off (or fetch the cached) AI analysis.
  useEffect(() => {
    if (!loaded) return;
    startAnalysis();
    let recent: string[] = [];
    try {
      recent = JSON.parse(readLS("finn.recent") ?? "[]");
    } catch {}
    writeLS("finn.recent", JSON.stringify([loaded, ...recent.filter((s) => s !== loaded)].slice(0, 6)));
    return () => {
      if (poll.current) clearInterval(poll.current);
    };
  }, [loaded, startAnalysis]);

  const n = nums.data;
  const result = job?.status === "done" ? job.result : undefined;
  const a = result?.analysis;

  return (
    <main>
      <div className="sticky top-0 z-30 border-b border-border bg-background/85 backdrop-blur-xl">
        <div className="flex items-center gap-3 px-4 pt-[max(env(safe-area-inset-top),0.75rem)] pb-2">
          <Link
            href="/research"
            onClick={(e) => {
              // Return to wherever you came from (Brief, Alerts, Portfolio…), at the same scroll spot.
              if (canGoBack()) {
                e.preventDefault();
                router.back();
              }
            }}
            className="-ml-1 rounded-full p-1 text-muted-foreground"
            aria-label="Back"
          >
            <ArrowLeft className="size-5" />
          </Link>
          <div className="min-w-0 flex-1">
            <p className="num text-xs text-muted-foreground">{symbol}</p>
            <p className="truncate text-[15px] font-medium">{n?.name ?? " "}</p>
          </div>
          {n?.market && (
            <div className="text-right">
              <p className="num text-[15px]">{price(n.market.close)}</p>
              <p className={cn("num text-xs", tone(n.market.change_pct))}>{pct(n.market.change_pct, 2)}</p>
            </div>
          )}
        </div>
        <nav className="no-scrollbar flex gap-4 overflow-x-auto px-4 pb-2 text-xs text-muted-foreground">
          {SECTIONS.map(([id, label]) => (
            <a key={id} href={`#${id}`} className="shrink-0 py-1 hover:text-foreground">
              {label}
            </a>
          ))}
        </nav>
      </div>

      {nums.error && (
        <div className="mt-6">
          <ErrorNote message={nums.error} onRetry={nums.reload} />
        </div>
      )}
      {nums.loading && (
        <div className="space-y-3 p-4">
          <Skeleton className="h-20" />
          <Skeleton className="h-40" />
          <Skeleton className="h-40" />
        </div>
      )}

      {n && (
        <div className="space-y-3 px-3 pt-4">
          {/* The one thing to know */}
          <div className="px-1">
            {a ? (
              <p className="font-serif text-[23px] leading-[1.25]">
                <Cite text={a.one_liner} />
              </p>
            ) : (
              <AnalysisStatus job={job} onRetry={() => startAnalysis(true)} />
            )}
          </div>

          <Snapshot n={n} a={a} />
          <Promises a={a} pending={!a} />
          <Story a={a} pending={!a} />
          <Trajectory n={n} />
          <Balance n={n} />
          <Cash n={n} />
          <Case a={a} pending={!a} />
          <AskPrompt symbol={symbol} name={n.name} />
          <Sources n={n} r={result} onRefresh={() => startAnalysis(true)} />
        </div>
      )}
    </main>
  );
}

// ---------- Pieces ----------

function Section({ id, title, children, note }: { id: string; title: string; children: React.ReactNode; note?: string }) {
  return (
    <section id={id} className="scroll-mt-28">
      <Card className="p-4">
        <div className="mb-3 flex items-baseline justify-between gap-2">
          <h2 className="text-[11px] font-medium tracking-[0.12em] text-muted-foreground uppercase">{title}</h2>
          {note && <span className="text-[10px] text-faint">{note}</span>}
        </div>
        {children}
      </Card>
    </section>
  );
}

function Pending({ lines = 3 }: { lines?: number }) {
  return (
    <div className="space-y-2">
      {Array.from({ length: lines }).map((_, i) => (
        <Skeleton key={i} className="h-4" />
      ))}
    </div>
  );
}

function AnalysisStatus({ job, onRetry }: { job: ResearchJob | null; onRetry: () => void }) {
  if (!job || job.status === "idle") return <Skeleton className="h-14" />;
  if (job.status === "no_ai")
    return (
      <p className="rounded-xl border border-dashed border-border p-3 text-sm text-muted-foreground">
        Showing computed numbers only. Add <code className="num text-foreground">GEMINI_API_KEY</code> to the backend for promises vs delivery, story vs numbers and bull/bear.
      </p>
    );
  if (job.status === "error")
    return (
      <div className="rounded-xl border border-down/30 bg-down/5 p-3 text-sm">
        <p className="text-down">{job.error}</p>
        <button onClick={onRetry} className="mt-1 text-xs text-muted-foreground underline">
          Try again
        </button>
      </div>
    );
  return (
    <div className="rounded-xl border border-border bg-card p-3">
      <p className="mb-2 text-xs text-muted-foreground">Reading primary sources…</p>
      <ul className="space-y-1.5">
        {(job.steps ?? []).map((s) => (
          <li key={s.label} className="flex items-center gap-2 text-[13px]">
            {s.state === "done" ? (
              <Check className="size-3.5 text-up" />
            ) : s.state === "running" ? (
              <Loader2 className="size-3.5 animate-spin text-signal" />
            ) : s.state === "error" ? (
              <X className="size-3.5 text-down" />
            ) : (
              <span className="mx-[3px] size-2 rounded-full border border-faint" />
            )}
            <span className={s.state === "pending" ? "text-faint" : ""}>{s.label}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

const FLAG_STYLE: Record<Flag["level"], string> = {
  good: "bg-up",
  warn: "bg-signal",
  bad: "bg-down",
  info: "bg-info",
};

function Flags({ flags }: { flags: Flag[] }) {
  if (!flags.length) return null;
  return (
    <ul className="mt-3 space-y-1.5">
      {flags.map((f, i) => (
        <li key={i} className="flex gap-2 text-[13px] leading-snug">
          <span className={cn("mt-[6px] size-1.5 shrink-0 rounded-full", FLAG_STYLE[f.level])} />
          {f.text}
        </li>
      ))}
    </ul>
  );
}

function Snapshot({ n, a }: { n: Numbers; a?: Analysis }) {
  const keys = ["Market Cap", "Stock P/E", "ROCE", "ROE", "Dividend Yield", "Book Value"];
  const ratios = keys.filter((k) => n.ratios[k]).map((k) => [k, n.ratios[k].replace("₹ ", "₹").replace(" Cr.", " Cr")]);
  const last = n.metrics.quarters[n.metrics.quarters.length - 1];
  return (
    <Section id="snapshot" title="Snapshot" note={`${n.basis} · ₹ crore`}>
      {a ? (
        <p className="text-[14px] leading-relaxed text-foreground/90">
          <Cite text={a.business} />
        </p>
      ) : null}
      {a && a.segments.length > 0 && (
        <div className="mt-3 space-y-2">
          {a.segments.map((s) => (
            <div key={s.name}>
              <div className="flex items-baseline justify-between text-[13px]">
                <span>{s.name}</span>
                {s.share_pct != null && <span className="num text-muted-foreground">{s.share_pct}%</span>}
              </div>
              {s.share_pct != null && (
                <div className="mt-1 h-1 overflow-hidden rounded-full bg-raised">
                  <div className="h-full rounded-full bg-foreground/60" style={{ width: `${Math.min(100, s.share_pct)}%` }} />
                </div>
              )}
              {s.note && <p className="mt-0.5 text-[11px] text-faint">{s.note}</p>}
            </div>
          ))}
        </div>
      )}
      <div className={cn("grid grid-cols-3 gap-px overflow-hidden rounded-xl bg-border", a && "mt-4")}>
        {ratios.map(([k, v]) => (
          <div key={k} className="bg-card px-2.5 py-2">
            <p className="text-[10px] text-faint">{k}</p>
            <p className="num mt-0.5 text-[13px]">{v}</p>
          </div>
        ))}
      </div>
      {last && (
        <div className="mt-3 grid grid-cols-3 gap-2 text-center">
          <Stat label={`Revenue ${last.period}`} value={cr(last.revenue)} sub={pct(last.revenue_yoy)} subTone={tone(last.revenue_yoy)} />
          <Stat label={n.metrics.lender ? "Fin. margin" : "Op. margin"} value={last.opm != null ? `${last.opm}%` : "–"} />
          <Stat label="Profit" value={cr(last.pat)} sub={pct(last.pat_yoy)} subTone={tone(last.pat_yoy)} />
        </div>
      )}
      <Flags flags={n.metrics.flags} />
    </Section>
  );
}

function Stat({ label, value, sub, subTone }: { label: string; value: string; sub?: string; subTone?: string }) {
  return (
    <div className="rounded-xl bg-raised px-2 py-2.5">
      <p className="truncate text-[10px] text-faint">{label}</p>
      <p className="num mt-0.5 text-[15px]">{value}</p>
      {sub && <p className={cn("num text-[11px]", subTone)}>{sub} YoY</p>}
    </div>
  );
}

const VERDICT: Record<string, string> = {
  delivered: "bg-up/15 text-up",
  agrees: "bg-up/15 text-up",
  partly: "bg-signal/15 text-signal",
  missed: "bg-down/15 text-down",
  diverges: "bg-down/15 text-down",
  pending: "bg-info/15 text-info",
  unclear: "bg-foreground/10 text-muted-foreground",
};

function Verdict({ v }: { v: string }) {
  const key = v.toLowerCase().trim();
  return (
    <span className={cn("shrink-0 rounded-full px-2 py-0.5 text-[10px] tracking-wider uppercase", VERDICT[key] ?? VERDICT.unclear)}>
      {key}
    </span>
  );
}

function Promises({ a, pending }: { a?: Analysis; pending: boolean }) {
  const counts = (a?.promises ?? []).reduce<Record<string, number>>((acc, p) => {
    const k = p.verdict.toLowerCase();
    acc[k] = (acc[k] ?? 0) + 1;
    return acc;
  }, {});
  return (
    <Section id="promises" title="Promised vs delivered" note="last 4 earnings calls">
      {pending ? (
        <Pending lines={4} />
      ) : a && a.promises.length ? (
        <>
          <div className="mb-3 flex flex-wrap gap-1.5">
            {Object.entries(counts).map(([k, c]) => (
              <span key={k} className={cn("rounded-full px-2 py-0.5 text-[11px]", VERDICT[k] ?? VERDICT.unclear)}>
                {c} {k}
              </span>
            ))}
          </div>
          <ul className="divide-y divide-border">
            {a.promises.map((p, i) => (
              <li key={i} className="py-3 first:pt-0 last:pb-0">
                <div className="flex items-start justify-between gap-2">
                  <p className="text-[11px] text-muted-foreground">
                    {p.metric} · <span className="text-faint">{p.made_in}</span>
                  </p>
                  <Verdict v={p.verdict} />
                </div>
                <p className="mt-1 text-[14px] leading-snug">
                  <span className="text-faint">Said: </span>
                  <Cite text={p.promised} />
                </p>
                <p className="mt-1 text-[14px] leading-snug">
                  <span className="text-faint">Did: </span>
                  <Cite text={p.delivered} />
                </p>
              </li>
            ))}
          </ul>
          {a.current_guidance.length > 0 && (
            <div className="mt-4 rounded-xl bg-raised p-3">
              <p className="mb-1.5 text-[11px] text-muted-foreground">Latest guidance</p>
              <ul className="space-y-1 text-[13px] leading-snug">
                {a.current_guidance.map((g, i) => (
                  <li key={i}>
                    <Cite text={g} />
                  </li>
                ))}
              </ul>
            </div>
          )}
        </>
      ) : (
        <p className="text-sm text-muted-foreground">No quantified guidance found in recent calls.</p>
      )}
    </Section>
  );
}

function Story({ a, pending }: { a?: Analysis; pending: boolean }) {
  return (
    <Section id="story" title="Story vs numbers">
      {pending ? (
        <Pending />
      ) : (
        <ul className="divide-y divide-border">
          {(a?.narrative_vs_numbers ?? []).map((d, i) => (
            <li key={i} className="py-3 first:pt-0 last:pb-0">
              <div className="flex items-start justify-between gap-2">
                <p className="text-[14px] leading-snug">
                  <Cite text={d.claim} />
                </p>
                <Verdict v={d.verdict} />
              </div>
              <p className="mt-1 text-[13px] leading-snug text-muted-foreground">
                <Cite text={d.numbers_say} />
              </p>
            </li>
          ))}
        </ul>
      )}
    </Section>
  );
}

function Trajectory({ n }: { n: Numbers }) {
  const q = n.metrics.quarters;
  const t = n.metrics.trajectory;
  const short = (p: string) => p.replace(/^(\w{3}) 20(\d\d)$/, "$1 $2");
  return (
    <Section id="trajectory" title="Trajectory">
      <div className="grid grid-cols-2 gap-2">
        <Stat label="Revenue CAGR 3y" value={pct(t.revenue_cagr_3y)} />
        <Stat label="Profit CAGR 3y" value={pct(t.pat_cagr_3y)} />
      </div>
      <p className="mt-4 mb-2 text-[11px] text-muted-foreground">Quarterly revenue · YoY growth</p>
      <Bars data={q.map((x) => x.revenue)} labels={q.map((x) => short(x.period))} />
      <div className="mt-1 flex gap-1">
        {q.map((x, i) => (
          <div key={i} className={cn("num flex-1 text-center text-[9px]", tone(x.revenue_yoy))}>
            {x.revenue_yoy != null ? `${x.revenue_yoy > 0 ? "+" : ""}${x.revenue_yoy.toFixed(0)}%` : ""}
          </div>
        ))}
      </div>
      <p className="mt-4 mb-2 text-[11px] text-muted-foreground">Quarterly profit</p>
      <Bars data={q.map((x) => x.pat)} labels={q.map((x) => short(x.period))} height={48} />
      <table className="mt-4 w-full text-[12px]">
        <thead>
          <tr className="text-[10px] text-faint">
            <th className="text-left font-normal">Year</th>
            <th className="text-right font-normal">Revenue</th>
            <th className="text-right font-normal">{n.metrics.lender ? "Fin. margin" : "OPM"}</th>
            <th className="text-right font-normal">Profit</th>
          </tr>
        </thead>
        <tbody className="num">
          {t.years.map((y) => (
            <tr key={y.year} className="border-t border-border/60">
              <td className="py-1.5 font-sans text-muted-foreground">{y.year}</td>
              <td className="py-1.5 text-right">{cr(y.revenue)}</td>
              <td className="py-1.5 text-right">{y.opm != null ? `${y.opm}%` : "–"}</td>
              <td className="py-1.5 text-right">{cr(y.pat)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </Section>
  );
}

function Balance({ n }: { n: Numbers }) {
  const b = n.metrics.balance;
  const wc = b.working_capital.filter((w) => w.ccc != null || w.debtor_days != null);
  return (
    <Section id="balance" title="Balance sheet">
      <table className="w-full text-[12px]">
        <thead>
          <tr className="text-[10px] text-faint">
            <th className="text-left font-normal">Year</th>
            <th className="text-right font-normal">Equity</th>
            <th className="text-right font-normal">{b.lender ? "Borrowing" : "Debt"}</th>
            {b.lender ? <th className="text-right font-normal">Deposits</th> : <th className="text-right font-normal">D/E</th>}
          </tr>
        </thead>
        <tbody className="num">
          {b.years.map((y) => (
            <tr key={y.year} className="border-t border-border/60">
              <td className="py-1.5 font-sans text-muted-foreground">{y.year}</td>
              <td className="py-1.5 text-right">{cr(y.equity)}</td>
              <td className="py-1.5 text-right">{cr(y.debt)}</td>
              <td className="py-1.5 text-right">{b.lender ? cr(y.deposits) : y.de ?? "–"}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {wc.length > 0 && (
        <>
          <p className="mt-4 mb-1 text-[11px] text-muted-foreground">Working capital (days)</p>
          <table className="w-full text-[12px]">
            <thead>
              <tr className="text-[10px] text-faint">
                <th className="text-left font-normal">Year</th>
                <th className="text-right font-normal">Debtors</th>
                <th className="text-right font-normal">Inventory</th>
                <th className="text-right font-normal">Payables</th>
                <th className="text-right font-normal">Cycle</th>
              </tr>
            </thead>
            <tbody className="num">
              {wc.map((w) => (
                <tr key={w.year} className="border-t border-border/60">
                  <td className="py-1.5 font-sans text-muted-foreground">{w.year}</td>
                  <td className="py-1.5 text-right">{w.debtor_days ?? "–"}</td>
                  <td className="py-1.5 text-right">{w.inventory_days ?? "–"}</td>
                  <td className="py-1.5 text-right">{w.payable_days ?? "–"}</td>
                  <td className="py-1.5 text-right font-medium">{w.ccc ?? "–"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}
      <Flags flags={n.metrics.flags.filter((f) => f.area === "balance")} />
    </Section>
  );
}

function Cash({ n }: { n: Numbers }) {
  const c = n.metrics.cash;
  return (
    <Section id="cash" title="Cash quality" note="does profit become cash?">
      <div className="grid grid-cols-2 gap-2">
        <Stat label="Cash / profit, 5y" value={c.conversion_5y != null ? `${Math.round(c.conversion_5y * 100)}%` : "–"} />
        <Stat label="Free cash flow, 5y" value={c.fcf_5y != null ? `₹${cr(c.fcf_5y)} Cr` : "–"} />
      </div>
      <table className="mt-4 w-full text-[12px]">
        <thead>
          <tr className="text-[10px] text-faint">
            <th className="text-left font-normal">Year</th>
            <th className="text-right font-normal">Op. cash</th>
            <th className="text-right font-normal">Profit</th>
            <th className="text-right font-normal">Conversion</th>
          </tr>
        </thead>
        <tbody className="num">
          {c.years.map((y) => (
            <tr key={y.year} className="border-t border-border/60">
              <td className="py-1.5 font-sans text-muted-foreground">{y.year}</td>
              <td className="py-1.5 text-right">{cr(y.cfo)}</td>
              <td className="py-1.5 text-right">{cr(y.pat)}</td>
              <td
                className={cn(
                  "py-1.5 text-right",
                  y.conversion == null ? "" : y.conversion >= 1 ? "text-up" : y.conversion < 0.7 ? "text-down" : "",
                )}
              >
                {y.conversion != null ? `${Math.round(y.conversion * 100)}%` : "–"}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <Flags flags={n.metrics.flags.filter((f) => f.area === "cash")} />
    </Section>
  );
}

function Case({ a, pending }: { a?: Analysis; pending: boolean }) {
  return (
    <Section id="case" title="Bull vs bear">
      {pending ? (
        <Pending lines={4} />
      ) : (
        a && (
          <div className="space-y-4">
            <CaseList title="Bull" tone="text-up" items={a.bull} />
            <CaseList title="Bear" tone="text-down" items={a.bear} />
            {a.watch.length > 0 && <CaseList title="Watch next quarter" tone="text-signal" items={a.watch} />}
          </div>
        )
      )}
    </Section>
  );
}

function CaseList({ title, tone: t, items }: { title: string; tone: string; items: string[] }) {
  return (
    <div>
      <p className={cn("mb-1.5 text-xs font-medium", t)}>{title}</p>
      <ul className="space-y-1.5">
        {items.map((x, i) => (
          <li key={i} className="flex gap-2 text-[14px] leading-snug">
            <span className="num mt-px text-[11px] text-faint">{i + 1}</span>
            <span>
              <Cite text={x} />
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}

const STARTERS = ["What is the capex plan?", "Why did margins change?", "What are analysts worried about?"];

/** Shortcut into the floating assistant, already set to this company. */
function AskPrompt({ symbol, name }: { symbol: string; name: string }) {
  return (
    <Section id="ask" title="Ask the filings">
      <div className="flex flex-wrap gap-1.5">
        {STARTERS.map((s) => (
          <button
            key={s}
            onClick={() => openChat({ symbol, name, question: s })}
            className="rounded-full border border-border px-2.5 py-1 text-xs text-muted-foreground hover:text-foreground"
          >
            {s}
          </button>
        ))}
      </div>
      <button
        onClick={() => openChat({ symbol, name })}
        className="mt-3 flex w-full items-center gap-2 rounded-xl border border-border bg-background px-3 py-2.5 text-left text-[14px] text-faint"
      >
        <MessageCircle className="size-4" />
        Ask anything about {name || symbol}…
      </button>
    </Section>
  );
}

function Sources({ n, r, onRefresh }: { n: Numbers; r?: ResearchResult; onRefresh: () => void }) {
  return (
    <div className="px-1 pt-2 pb-6 text-[11px] leading-relaxed text-faint">
      <p className="mb-1 text-muted-foreground">Sources (primary only)</p>
      <div className="flex flex-wrap gap-x-3 gap-y-1">
        <a href={n.source_url} target="_blank" rel="noreferrer" className="inline-flex items-center gap-0.5 underline underline-offset-2">
          Financials <ArrowUpRight className="size-3" />
        </a>
        {r?.sources.annual_report && (
          <a href={r.sources.annual_report.url} target="_blank" rel="noreferrer" className="inline-flex items-center gap-0.5 underline underline-offset-2">
            Annual report {r.sources.annual_report.year}
            {r.sources.ar_pages.length > 0 && ` (pp. ${r.sources.ar_pages.slice(0, 6).join(", ")})`} <ArrowUpRight className="size-3" />
          </a>
        )}
        {r?.sources.transcripts.map((t) => (
          <a key={t.url} href={t.url} target="_blank" rel="noreferrer" className="inline-flex items-center gap-0.5 underline underline-offset-2">
            {t.period} call <ArrowUpRight className="size-3" />
          </a>
        ))}
      </div>
      {r && (
        <div className="mt-3 flex items-center justify-between">
          <span>
            {r.usage.calls} model calls · {tokens(r.usage.input_tokens)} in / {tokens(r.usage.output_tokens)} out ·{" "}
            {Object.keys(r.usage.models).join(" + ")}
          </span>
          <button onClick={onRefresh} className="inline-flex items-center gap-1 text-muted-foreground" aria-label="Refresh analysis">
            <RefreshCw className="size-3" />
          </button>
        </div>
      )}
    </div>
  );
}
