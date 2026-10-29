"use client";

import Link from "next/link";
import { useState } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { ArrowUpRight, ChevronDown, Loader2 } from "lucide-react";
import { api, type Filing, type ManagementInsight, type ResultsInsight } from "@/lib/api";
import { ago, cr, pct, tone } from "@/lib/format";
import { cn } from "@/lib/utils";

/** Strip exchange boilerplate so the headline starts with what happened. */
export function headline(f: Filing) {
  if (f.summary) return f.summary;
  const t = f.text
    .replace(new RegExp(`^${f.company.split(" ")[0]}[^,]*?has (informed|submitted to|filed with) (the )?exchange,?\\s*(about|regarding|that)?\\s*`, "i"), "")
    .replace(/^['"\s]+|['"\s.]+$/g, "");
  const s = t || f.title;
  return s.charAt(0).toUpperCase() + s.slice(1);
}

const INSIGHT_LABEL = { results: "Headline numbers", management: "Who's involved" } as const;

export function FilingCard({ f, isNew, compact }: { f: Filing; isNew?: boolean; compact?: boolean }) {
  const [open, setOpen] = useState(false);
  const [insight, setInsight] = useState<ResultsInsight | ManagementInsight | null>(null);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  async function toggle() {
    const next = !open;
    setOpen(next);
    if (next && !insight && f.insight) {
      setLoading(true);
      setErr(null);
      try {
        setInsight(await api(`/filings/${f.id}/insight`));
      } catch (e) {
        setErr(e instanceof Error ? e.message : "Couldn't load");
      } finally {
        setLoading(false);
      }
    }
  }

  return (
    <article
      className={cn(
        "relative px-4 py-3.5",
        !compact && "mx-3 mb-2.5 overflow-hidden rounded-2xl border border-border bg-card px-4 py-4",
      )}
    >
      {!compact && f.tier === "attention" && <span className="absolute inset-y-4 left-0 w-[3px] rounded-r bg-signal" />}
      <div className="flex items-center gap-2 text-[11px] text-muted-foreground">
        <Link href={`/research/${f.symbol}`} className="num font-medium text-foreground hover:underline">
          {f.symbol}
        </Link>
        <span className="text-faint">·</span>
        <span>{f.category_label}</span>
        <span className="text-faint">·</span>
        <span>{ago(f.time)}</span>
        {isNew && <span className="ml-auto size-1.5 rounded-full bg-signal" aria-label="new" />}
      </div>
      <p className={cn("mt-1.5 leading-snug", compact ? "text-[14px] text-foreground/90" : "text-[15px]")}>{headline(f)}</p>

      <div className="mt-3 flex items-center gap-4 text-xs text-muted-foreground">
        {f.insight && (
          <button onClick={toggle} className="flex items-center gap-1 font-medium text-foreground">
            {INSIGHT_LABEL[f.insight]}
            <ChevronDown className={cn("size-3.5 transition-transform", open && "rotate-180")} />
          </button>
        )}
        {!f.insight && f.summary && f.text && (
          <button onClick={() => setOpen(!open)} className="flex items-center gap-1">
            Filing text
            <ChevronDown className={cn("size-3.5 transition-transform", open && "rotate-180")} />
          </button>
        )}
        {f.url && (
          <a href={f.url} target="_blank" rel="noreferrer" className="flex items-center gap-0.5 hover:text-foreground">
            PDF <ArrowUpRight className="size-3" />
          </a>
        )}
        <Link href={`/research/${f.symbol}`} className="ml-auto hover:text-foreground">
          Dig in →
        </Link>
      </div>

      <AnimatePresence initial={false}>
        {open && (
          <motion.div
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: "auto", opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ duration: 0.2 }}
            className="overflow-hidden"
          >
            <div className="pt-3">
              {loading && (
                <div className="flex items-center gap-2 py-3 text-xs text-muted-foreground">
                  <Loader2 className="size-3.5 animate-spin" />
                  {f.insight === "results" ? "Reading the results filing…" : "Reading the filing and looking them up…"}
                </div>
              )}
              {err && <p className="text-xs text-down">{err}</p>}
              {insight?.kind === "results" && <ResultsTable r={insight} />}
              {insight?.kind === "management" && <People m={insight} />}
              {!f.insight && <p className="text-xs leading-relaxed text-muted-foreground">{f.text}</p>}
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </article>
  );
}

function ResultsTable({ r }: { r: ResultsInsight }) {
  if (r.error || !r.rows) return <p className="text-xs text-muted-foreground">{r.error}</p>;
  return (
    <div className="rounded-xl bg-raised p-3">
      <div className="flex items-baseline justify-between text-[11px] text-muted-foreground">
        <span className="font-medium text-foreground">{r.periods.current}</span>
        <span>
          {r.basis} · {r.unit}
        </span>
      </div>
      <table className="mt-2 w-full text-sm">
        <thead>
          <tr className="text-[10px] tracking-wider text-faint uppercase">
            <th className="py-1 text-left font-normal" />
            <th className="py-1 text-right font-normal">Value</th>
            <th className="py-1 text-right font-normal">QoQ</th>
            <th className="py-1 text-right font-normal">YoY</th>
          </tr>
        </thead>
        <tbody>
          {r.rows.map((row) => (
            <tr key={row.metric} className="border-t border-border/60">
              <td className="py-1.5 text-muted-foreground">{row.metric}</td>
              {row.na ? (
                <td colSpan={3} className="py-1.5 text-right text-xs text-faint">
                  not meaningful for lenders
                </td>
              ) : (
                <>
                  <td className="num py-1.5 text-right">{cr(row.value)}</td>
                  <td className={cn("num py-1.5 text-right", tone(row.qoq_pct))}>{pct(row.qoq_pct)}</td>
                  <td className={cn("num py-1.5 text-right font-medium", tone(row.yoy_pct))}>{pct(row.yoy_pct)}</td>
                </>
              )}
            </tr>
          ))}
        </tbody>
      </table>
      {r.read && <p className="mt-2.5 text-[13px] leading-snug">{r.read}</p>}
      <p className="mt-2 text-[10px] text-faint">
        vs {r.periods.previous} and {r.periods.year_ago} · from {r.source}
        {r.pages?.length ? ` (p. ${r.pages.join(", ")})` : ""}
        {!r.is_lender && " · EBITDA = PBT + finance cost + D&A − other income"}
      </p>
    </div>
  );
}

function People({ m }: { m: ManagementInsight }) {
  if (m.error || !m.people) return <p className="text-xs text-muted-foreground">{m.error}</p>;
  return (
    <div className="space-y-2">
      {m.people.map((p) => (
        <div key={p.name + p.role} className="rounded-xl bg-raised p-3">
          <div className="flex items-start justify-between gap-2">
            <div>
              <p className="font-medium">{p.name}</p>
              <p className="text-xs text-muted-foreground">{p.role}</p>
            </div>
            <span
              className={cn(
                "shrink-0 rounded-full px-2 py-0.5 text-[10px] uppercase tracking-wider",
                p.change === "appointed" ? "bg-up/15 text-up" : p.change === "resigned" ? "bg-down/15 text-down" : "bg-foreground/10 text-muted-foreground",
              )}
            >
              {p.change}
            </span>
          </div>
          {(p.effective_date || p.reason) && (
            <p className="mt-1.5 text-xs text-muted-foreground">
              {p.effective_date && <>From {p.effective_date}. </>}
              {p.reason && <>Reason: {p.reason}</>}
            </p>
          )}
          {p.bio ? (
            <ul className="mt-2 space-y-1 text-[13px] leading-snug">
              {p.bio
                .split("\n")
                .map((l) => l.replace(/^[\s*•-]+/, "").replace(/\*\*/g, "").trim())
                .filter(Boolean)
                .map((l, i) => (
                  <li key={i} className="flex gap-2">
                    <span className="mt-[7px] size-1 shrink-0 rounded-full bg-muted-foreground" />
                    {l}
                  </li>
                ))}
            </ul>
          ) : (
            p.profile_from_filing && <p className="mt-2 text-[13px] leading-snug">{p.profile_from_filing}</p>
          )}
          {p.sources.length > 0 && (
            <div className="mt-2 flex flex-wrap gap-x-3 gap-y-1">
              {p.sources.map((s) => (
                <a key={s.url} href={s.url} target="_blank" rel="noreferrer" className="text-[10px] text-faint underline underline-offset-2">
                  {s.title}
                </a>
              ))}
            </div>
          )}
        </div>
      ))}
    </div>
  );
}
