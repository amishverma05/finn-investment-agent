"use client";

import Link from "next/link";
import { useMemo, useState } from "react";
import { Plus, Trash2 } from "lucide-react";
import { api, useApi, useSearch, type Holding, type SearchHit } from "@/lib/api";
import { pct, price, tone } from "@/lib/format";
import { cn } from "@/lib/utils";
import { Sparkline } from "@/components/sparkline";
import { Chip, ErrorNote, Header, Skeleton } from "@/components/ui-bits";

type Sort = "move" | "name" | "volume";

export default function PortfolioPage() {
  const book = useApi<{ items: Holding[] }>("/portfolio");
  const [editing, setEditing] = useState(false);
  const [sort, setSort] = useState<Sort>("move");
  const [q, setQ] = useState("");
  const hits = useSearch(q);
  const [err, setErr] = useState<string | null>(null);

  const items = useMemo(() => {
    const list = [...(book.data?.items ?? [])];
    if (sort === "name") return list.sort((a, b) => a.symbol.localeCompare(b.symbol));
    if (sort === "volume") return list.sort((a, b) => (b.market?.volume_ratio ?? 0) - (a.market?.volume_ratio ?? 0));
    return list.sort((a, b) => Math.abs(b.market?.change_pct ?? 0) - Math.abs(a.market?.change_pct ?? 0));
  }, [book.data, sort]);

  const breadth = useMemo(() => {
    const ms = (book.data?.items ?? []).map((h) => h.market).filter(Boolean);
    const up = ms.filter((m) => m!.change_pct > 0).length;
    const avg = ms.length ? ms.reduce((s, m) => s + m!.change_pct, 0) / ms.length : 0;
    return { up, down: ms.length - up, avg };
  }, [book.data]);

  async function add(h: SearchHit) {
    setErr(null);
    try {
      await api("/portfolio", { method: "POST", body: JSON.stringify({ symbol: h.symbol, name: h.name }) });
      setQ("");
      book.reload();
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Couldn't add");
    }
  }

  async function remove(symbol: string) {
    await api(`/portfolio/${symbol}`, { method: "DELETE" });
    book.reload();
  }

  return (
    <main>
      <Header
        eyebrow={`${book.data?.items.length ?? ""} holdings`}
        title="Portfolio"
        right={
          <button onClick={() => setEditing(!editing)} className="text-sm text-muted-foreground">
            {editing ? "Done" : "Edit"}
          </button>
        }
      />

      {book.data && (
        <p className="px-4 text-sm text-muted-foreground">
          <span className="text-up">{breadth.up} up</span>, <span className="text-down">{breadth.down} down</span> at the last close ·
          average move <span className={cn("num", tone(breadth.avg))}>{pct(breadth.avg, 2)}</span>
        </p>
      )}

      {editing && (
        <div className="mt-4 px-4">
          <div className="flex items-center gap-2 rounded-xl border border-border bg-card px-3 py-2.5">
            <Plus className="size-4 text-muted-foreground" />
            <input
              value={q}
              onChange={(e) => setQ(e.target.value)}
              placeholder="Add a company"
              className="w-full bg-transparent text-sm outline-none placeholder:text-faint"
            />
          </div>
          {hits.length > 0 && (
            <ul className="mt-2 divide-y divide-border overflow-hidden rounded-xl border border-border bg-card">
              {hits.map((h) => (
                <li key={h.symbol}>
                  <button onClick={() => add(h)} className="flex w-full items-center justify-between px-3 py-2.5 text-left">
                    <span className="text-sm">{h.name}</span>
                    <span className="num text-xs text-muted-foreground">{h.symbol}</span>
                  </button>
                </li>
              ))}
            </ul>
          )}
          {err && <p className="mt-2 text-xs text-down">{err}</p>}
        </div>
      )}

      <div className="no-scrollbar mt-4 flex gap-2 px-4">
        <Chip active={sort === "move"} onClick={() => setSort("move")}>
          Biggest move
        </Chip>
        <Chip active={sort === "volume"} onClick={() => setSort("volume")}>
          Unusual volume
        </Chip>
        <Chip active={sort === "name"} onClick={() => setSort("name")}>
          A–Z
        </Chip>
      </div>

      {book.error && (
        <div className="mt-4">
          <ErrorNote message={book.error} onRetry={book.reload} />
        </div>
      )}

      <ul className="mx-3 mt-3 divide-y divide-border overflow-hidden rounded-2xl border border-border bg-card">
        {book.loading && !book.data
          ? Array.from({ length: 8 }).map((_, i) => (
              <li key={i} className="p-3">
                <Skeleton className="h-9" />
              </li>
            ))
          : items.map((h) => {
              const m = h.market;
              return (
                <li key={h.symbol} className="flex items-center gap-3 px-4 py-3">
                  <Link href={`/research/${h.symbol}`} className="flex min-w-0 flex-1 items-center gap-3">
                    <div className="min-w-0 flex-1">
                      <p className="num text-[13px] font-medium">{h.symbol}</p>
                      <p className="truncate text-[11px] text-muted-foreground">
                        {h.name}
                        {h.sector && <span className="text-faint"> · {h.sector}</span>}
                      </p>
                    </div>
                    {m && <Sparkline data={m.spark} width={56} height={22} />}
                    <div className="w-[84px] text-right">
                      <p className="num text-[13px]">{m ? price(m.close) : "–"}</p>
                      <p className={cn("num text-[11px]", tone(m?.change_pct))}>
                        {m ? pct(m.change_pct, 2) : ""}
                        {m?.volume_ratio != null && m.volume_ratio >= 1.5 && (
                          <span className="ml-1 text-signal">{m.volume_ratio}×</span>
                        )}
                      </p>
                    </div>
                  </Link>
                  {editing && (
                    <button onClick={() => remove(h.symbol)} className="p-1 text-down" aria-label={`Remove ${h.symbol}`}>
                      <Trash2 className="size-4" />
                    </button>
                  )}
                </li>
              );
            })}
      </ul>
      {book.data && (
        <p className="mt-3 px-4 text-center text-[10px] text-faint">
          End-of-day prices from Yahoo Finance · amber = volume at least 1.5× its 20-day average
        </p>
      )}
    </main>
  );
}
