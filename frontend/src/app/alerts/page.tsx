"use client";

import Link from "next/link";
import { useMemo, useRef, useState } from "react";
import { SlidersHorizontal } from "lucide-react";
import { api, useApi, type Alerts, type Filing, type MarketAlert, type Settings } from "@/lib/api";
import { headline, FilingCard } from "@/components/filing-card";
import { pct, price, tone } from "@/lib/format";
import { cn } from "@/lib/utils";
import { Card, Chip, ErrorNote, Header, SectionTitle, Skeleton } from "@/components/ui-bits";

const CATEGORY_OPTIONS: [string, string][] = [
  ["results", "Results"],
  ["M&A", "M&A"],
  ["management_change", "Management"],
  ["litigation", "Legal"],
  ["fund_raise", "Fund raise"],
  ["credit_rating", "Credit rating"],
  ["pledging", "Pledge"],
  ["insider_trading", "Insider trades"],
  ["capex", "Capex"],
  ["dividend", "Dividend"],
  ["bonus_split", "Bonus / split"],
];

/** What the combination of price and volume usually means. */
function readMove(a: MarketAlert) {
  const v = a.volume_ratio ?? 1;
  const dir = a.change_pct >= 0 ? "Rose" : "Fell";
  if (a.price_trigger && v >= 2) return `${dir} ${Math.abs(a.change_pct).toFixed(1)}% on ${v}× normal volume — a conviction move.`;
  if (a.price_trigger && v < 1) return `${dir} ${Math.abs(a.change_pct).toFixed(1)}% on thin volume (${v}×) — little conviction behind it.`;
  if (a.price_trigger) return `${dir} ${Math.abs(a.change_pct).toFixed(1)}% on ${v}× normal volume.`;
  return `Volume ${v}× normal while price moved ${pct(a.change_pct)} — heavy trading without a clear direction.`;
}

export default function AlertsPage() {
  const [hours, setHours] = useState(72);
  const [editing, setEditing] = useState(false);
  const alerts = useApi<Alerts>(`/alerts?hours=${hours}`);
  const [draft, setDraft] = useState<Settings | null>(null);
  const rules = draft ?? alerts.data?.settings ?? null;
  const saveTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  function update(patch: Partial<Settings>) {
    if (!rules) return;
    const next = { ...rules, ...patch };
    setDraft(next);
    if (saveTimer.current) clearTimeout(saveTimer.current);
    saveTimer.current = setTimeout(async () => {
      await api("/settings", { method: "PUT", body: JSON.stringify(next) });
      alerts.reload();
    }, 500);
  }

  const feed = useMemo(() => {
    if (!alerts.data) return [];
    const filings = alerts.data.filings;
    const market = alerts.data.market.map((m) => ({
      ...m,
      related: filings.find(
        (f) =>
          f.symbol === m.symbol &&
          Math.abs(new Date(f.time).getTime() - new Date(m.date + "T15:30:00+05:30").getTime()) < 2.5 * 864e5,
      ),
    }));
    type Row = { t: number; market?: (typeof market)[number]; filing?: Filing };
    const rows: Row[] = [
      ...market.map((m) => ({ t: new Date(m.date + "T15:30:00+05:30").getTime(), market: m })),
      ...filings.map((f) => ({ t: new Date(f.time).getTime(), filing: f })),
    ];
    return rows.sort((a, b) => b.t - a.t);
  }, [alerts.data]);

  return (
    <main>
      <Header
        eyebrow="You decide what's unusual"
        title="Alerts"
        right={
          <button
            onClick={() => setEditing(!editing)}
            className={cn("rounded-full border p-2", editing ? "border-foreground bg-foreground text-background" : "border-border")}
            aria-label="Edit rules"
          >
            <SlidersHorizontal className="size-4" />
          </button>
        }
      />

      {/* Rules, always visible as a one-line summary; editable on tap */}
      {rules && (
        <div className="px-3">
          <Card className="p-4">
            {!editing ? (
              <button onClick={() => setEditing(true)} className="w-full text-left text-sm leading-relaxed text-muted-foreground">
                Watching for moves beyond <b className="num text-foreground">±{rules.price_move_pct}%</b>, volume above{" "}
                <b className="num text-foreground">{rules.volume_multiple}×</b> its {rules.volume_lookback}-day average, and{" "}
                <b className="text-foreground">{rules.alert_categories.length} filing types</b>.
              </button>
            ) : (
              <div className="space-y-5">
                <Slider
                  label="Daily price move beyond"
                  value={rules.price_move_pct}
                  min={1}
                  max={10}
                  step={0.5}
                  format={(v) => `±${v}%`}
                  onChange={(v) => update({ price_move_pct: v })}
                />
                <Slider
                  label={`Volume above its ${rules.volume_lookback}-day average by`}
                  value={rules.volume_multiple}
                  min={1.2}
                  max={5}
                  step={0.1}
                  format={(v) => `${v.toFixed(1)}×`}
                  onChange={(v) => update({ volume_multiple: Math.round(v * 10) / 10 })}
                />
                <div>
                  <p className="mb-2 text-xs text-muted-foreground">Filings that should alert me</p>
                  <div className="flex flex-wrap gap-1.5">
                    {CATEGORY_OPTIONS.map(([key, label]) => {
                      const on = rules.alert_categories.includes(key);
                      return (
                        <Chip
                          key={key}
                          active={on}
                          onClick={() =>
                            update({
                              alert_categories: on
                                ? rules.alert_categories.filter((c) => c !== key)
                                : [...rules.alert_categories, key],
                            })
                          }
                        >
                          {label}
                        </Chip>
                      );
                    })}
                  </div>
                </div>
              </div>
            )}
          </Card>
        </div>
      )}

      <div className="no-scrollbar mt-4 flex gap-2 overflow-x-auto px-4">
        {[
          [24, "Today"],
          [72, "3 days"],
          [168, "1 week"],
        ].map(([h, l]) => (
          <Chip key={h} active={hours === h} onClick={() => setHours(h as number)}>
            {l}
          </Chip>
        ))}
      </div>

      <SectionTitle count={feed.length}>Triggered</SectionTitle>

      {alerts.error && <ErrorNote message={alerts.error} onRetry={alerts.reload} />}
      {alerts.loading && !alerts.data && (
        <div className="space-y-2 px-3">
          {Array.from({ length: 4 }).map((_, i) => (
            <Skeleton key={i} className="h-20" />
          ))}
        </div>
      )}

      <div className={cn("transition-opacity", alerts.loading && alerts.data && "opacity-50")}>
        {alerts.data && feed.length === 0 && (
          <p className="px-4 text-sm text-muted-foreground">Nothing crossed your thresholds. Quiet is good.</p>
        )}
        {feed.map((row) =>
          row.market ? (
            <MarketRow key={`m-${row.market.symbol}-${row.market.date}`} a={row.market} related={row.market.related} />
          ) : (
            <FilingCard key={`f-${row.filing!.id}`} f={row.filing!} />
          ),
        )}
      </div>
    </main>
  );
}

function MarketRow({ a, related }: { a: MarketAlert; related?: Filing }) {
  return (
    <Link href={`/research/${a.symbol}`} className="mx-3 mb-2.5 block rounded-2xl border border-border bg-card p-4 active:scale-[0.99]">
      <div className="flex items-center gap-2 text-[11px] text-muted-foreground">
        <span className="num font-medium text-foreground">{a.symbol}</span>
        <span className="text-faint">·</span>
        <span>Market move</span>
        <span className="text-faint">·</span>
        <span>{new Date(a.date).toLocaleDateString("en-IN", { day: "numeric", month: "short" })}</span>
        <span className="num ml-auto text-foreground">{price(a.close)}</span>
      </div>
      <div className="mt-2 flex items-center gap-2">
        <span className={cn("num text-xl font-medium", tone(a.change_pct))}>{pct(a.change_pct, 2)}</span>
        {a.volume_ratio != null && (
          <span
            className={cn(
              "num rounded-md px-1.5 py-0.5 text-[11px]",
              a.volume_trigger ? "bg-signal/15 text-signal" : "bg-foreground/5 text-muted-foreground",
            )}
          >
            {a.volume_ratio}× vol
          </span>
        )}
      </div>
      <p className="mt-1.5 text-[13px] leading-snug text-muted-foreground">{readMove(a)}</p>
      {related && (
        <p className="mt-2 rounded-lg bg-raised px-2.5 py-1.5 text-xs leading-snug">
          <span className="text-faint">Possibly related: </span>
          {headline(related)}
        </p>
      )}
    </Link>
  );
}

function Slider({
  label,
  value,
  min,
  max,
  step,
  format,
  onChange,
}: {
  label: string;
  value: number;
  min: number;
  max: number;
  step: number;
  format: (v: number) => string;
  onChange: (v: number) => void;
}) {
  return (
    <label className="block">
      <div className="mb-2 flex items-baseline justify-between">
        <span className="text-xs text-muted-foreground">{label}</span>
        <span className="num text-sm font-medium">{format(value)}</span>
      </div>
      <input
        type="range"
        min={min}
        max={max}
        step={step}
        value={value}
        onChange={(e) => onChange(Number(e.target.value))}
        className="w-full accent-[var(--signal)]"
      />
    </label>
  );
}
