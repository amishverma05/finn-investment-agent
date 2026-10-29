const inr = new Intl.NumberFormat("en-IN", { maximumFractionDigits: 0 });
const inr2 = new Intl.NumberFormat("en-IN", { maximumFractionDigits: 2, minimumFractionDigits: 2 });

export function price(n: number | null | undefined) {
  return n == null ? "–" : `₹${inr2.format(n)}`;
}

/** Crore figures: 78750 -> "78,750" */
export function cr(n: number | null | undefined) {
  if (n == null) return "–";
  return Math.abs(n) >= 100 ? inr.format(n) : n.toFixed(1);
}

export function pct(n: number | null | undefined, digits = 1) {
  if (n == null) return "–";
  return `${n > 0 ? "+" : ""}${n.toFixed(digits)}%`;
}

export function tone(n: number | null | undefined) {
  if (n == null || n === 0) return "text-muted-foreground";
  return n > 0 ? "text-up" : "text-down";
}

export function volume(n: number | null | undefined) {
  if (n == null) return "–";
  if (n >= 1e7) return `${(n / 1e7).toFixed(1)} Cr`;
  if (n >= 1e5) return `${(n / 1e5).toFixed(1)} L`;
  return inr.format(n);
}

export function ago(iso: string) {
  const s = (Date.now() - new Date(iso).getTime()) / 1000;
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  const d = Math.floor(s / 86400);
  return d === 1 ? "yesterday" : `${d}d ago`;
}

export function tokens(n: number) {
  return n >= 1000 ? `${(n / 1000).toFixed(1)}k` : `${n}`;
}
