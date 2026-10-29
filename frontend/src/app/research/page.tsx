"use client";

import Link from "next/link";
import { useMemo, useState } from "react";
import { Search } from "lucide-react";
import { useApi, useSearch, useStored } from "@/lib/api";
import { Header, SectionTitle } from "@/components/ui-bits";

export default function ResearchPage() {
  const [q, setQ] = useState("");
  const hits = useSearch(q);
  const suggestions = useApi<string[]>("/research/suggestions");
  const stored = useStored("finn.recent");
  const recent = useMemo<string[]>(() => {
    try {
      return JSON.parse(stored ?? "[]");
    } catch {
      return [];
    }
  }, [stored]);

  return (
    <main>
      <Header eyebrow="Grounded in annual reports & earnings calls" title="Research" />
      <div className="px-4">
        <div className="flex items-center gap-2 rounded-2xl border border-border bg-card px-3.5 py-3 focus-within:border-ring">
          <Search className="size-4 text-muted-foreground" />
          <input
            autoFocus
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="Any NSE company or ticker"
            className="w-full bg-transparent text-[15px] outline-none placeholder:text-faint"
          />
        </div>
        {hits.length > 0 && (
          <ul className="mt-2 divide-y divide-border overflow-hidden rounded-2xl border border-border bg-card">
            {hits.map((h) => (
              <li key={h.symbol}>
                <Link href={`/research/${h.symbol}`} className="flex items-center justify-between px-4 py-3 active:bg-raised">
                  <span className="text-sm">{h.name}</span>
                  <span className="num text-xs text-muted-foreground">{h.symbol}</span>
                </Link>
              </li>
            ))}
          </ul>
        )}
      </div>

      {recent.length > 0 && (
        <>
          <SectionTitle>Recent</SectionTitle>
          <div className="flex flex-wrap gap-2 px-4">
            {recent.map((s) => (
              <Link key={s} href={`/research/${s}`} className="num rounded-full border border-border px-3 py-1.5 text-xs">
                {s}
              </Link>
            ))}
          </div>
        </>
      )}

      <SectionTitle>Try</SectionTitle>
      <div className="flex flex-wrap gap-2 px-4">
        {(suggestions.data ?? []).map((s) => (
          <Link key={s} href={`/research/${s}`} className="num rounded-full border border-border px-3 py-1.5 text-xs">
            {s}
          </Link>
        ))}
      </div>

      <div className="mx-4 mt-8 rounded-2xl border border-dashed border-border p-4 text-[13px] leading-relaxed text-muted-foreground">
        <p className="mb-1 font-medium text-foreground">How FINN researches</p>
        Numbers are computed from the company&apos;s reported financials, never generated. The AI reads the last four
        earnings-call transcripts and the latest annual report — nothing else: no news, no broker notes — and checks
        what management promised against what was delivered.
      </div>
    </main>
  );
}
