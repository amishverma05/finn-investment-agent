"use client";

import { useCallback, useEffect, useState, useSyncExternalStore } from "react";

export const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_URL}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      detail = (await res.json()).detail ?? detail;
    } catch {}
    throw new Error(detail);
  }
  return res.json();
}

// Last response per path, kept in memory for this tab. When you come back to a screen it
// renders straight away with this (then refreshes), so the page has its full height and
// your scroll position can be restored.
const memo = new Map<string, unknown>();

/** Fetch `path` and keep the last good data while a new request is in flight. */
export function useApi<T>(path: string | null) {
  const [nonce, setNonce] = useState(0);
  const [state, setState] = useState<{ key: string | null; data: T | null; error: string | null }>({
    key: null,
    data: null,
    error: null,
  });
  const key = path ? `${path}#${nonce}` : null;

  useEffect(() => {
    if (!path) return;
    let alive = true;
    const k = `${path}#${nonce}`;
    api<T>(path)
      .then((data) => {
        memo.set(path, data);
        if (alive) setState({ key: k, data, error: null });
      })
      .catch((e) => alive && setState((s) => ({ ...s, key: k, error: e instanceof Error ? e.message : "Something went wrong" })));
    return () => {
      alive = false;
    };
  }, [path, nonce]);

  const reload = useCallback(() => setNonce((n) => n + 1), []);
  const fresh = state.key === key;
  const data = fresh ? state.data : ((path ? (memo.get(path) as T | undefined) : undefined) ?? state.data);
  return { data, error: state.error, loading: !!key && !fresh, reload };
}

export type SearchHit = { symbol: string; name: string };

/** Debounced company search. */
export function useSearch(q: string) {
  const [res, setRes] = useState<SearchHit[]>([]);
  const term = q.trim();
  useEffect(() => {
    if (term.length < 2) return;
    const t = setTimeout(() => {
      api<SearchHit[]>(`/search?q=${encodeURIComponent(term)}`)
        .then(setRes)
        .catch(() => setRes([]));
    }, 250);
    return () => clearTimeout(t);
  }, [term]);
  return term.length >= 2 ? res : [];
}

// ---------- localStorage, read safely (it can be missing or blocked) ----------

export function readLS(key: string): string | null {
  try {
    return localStorage.getItem(key);
  } catch {
    return null;
  }
}

export function writeLS(key: string, value: string) {
  try {
    localStorage.setItem(key, value);
    window.dispatchEvent(new StorageEvent("storage", { key }));
  } catch {}
}

function subscribeLS(cb: () => void) {
  window.addEventListener("storage", cb);
  return () => window.removeEventListener("storage", cb);
}

/** A localStorage value that is null during server render and updates when written. */
export function useStored(key: string): string | null {
  return useSyncExternalStore(
    subscribeLS,
    () => readLS(key),
    () => null,
  );
}

// ---------- Types ----------

export type Usage = { calls: number; input_tokens: number; output_tokens: number; models: Record<string, number> };

export type Market = {
  symbol: string;
  date: string;
  close: number;
  prev_close: number;
  change_pct: number;
  volume: number;
  avg_volume: number | null;
  volume_ratio: number | null;
  change_1m_pct: number | null;
  spark: number[];
};

export type Holding = { symbol: string; name: string; sector: string; market?: Market | null };

export type Tier = "attention" | "notable" | "routine";

export type Filing = {
  id: string;
  symbol: string;
  company: string;
  title: string;
  text: string;
  summary: string | null;
  time: string;
  url: string | null;
  category: string;
  category_label: string;
  importance: number;
  tier: Tier;
  insight: "results" | "management" | null;
};

export type Digest = {
  since: string;
  until: string;
  hours: number;
  counts: Record<Tier, number>;
  items: Filing[];
  ai: boolean;
  /** Filings still waiting for an AI one-liner (fast view only). */
  ai_pending?: number;
  usage: Usage;
};

export type ResultsRow = {
  metric: string;
  value: number | null;
  prev: number | null;
  year_ago: number | null;
  qoq_pct: number | null;
  yoy_pct: number | null;
  na: boolean;
};

export type ResultsInsight = {
  kind: "results";
  source: string;
  basis: string;
  unit: string;
  is_lender: boolean;
  periods: { current: string; previous: string; year_ago: string };
  rows: ResultsRow[];
  read: string | null;
  pages?: number[];
  error?: string;
};

export type Person = {
  name: string;
  role: string;
  change: string;
  effective_date: string | null;
  reason: string | null;
  profile_from_filing: string | null;
  bio: string | null;
  sources: { title: string; url: string }[];
};

export type ManagementInsight = { kind: "management"; people?: Person[]; error?: string };

export type Settings = {
  price_move_pct: number;
  volume_multiple: number;
  volume_lookback: number;
  alert_categories: string[];
};

export type MarketAlert = {
  kind: "market";
  symbol: string;
  company: string;
  date: string;
  change_pct: number;
  volume_ratio: number | null;
  close: number;
  price_trigger: boolean;
  volume_trigger: boolean;
};

export type Alerts = { settings: Settings; market: MarketAlert[]; filings: Filing[] };

export type Flag = { level: "good" | "warn" | "bad" | "info"; area: string; text: string };

export type Metrics = {
  lender: boolean;
  quarters: {
    period: string;
    revenue: number | null;
    op_profit: number | null;
    opm: number | null;
    pat: number | null;
    revenue_yoy: number | null;
    pat_yoy: number | null;
  }[];
  trajectory: {
    years: { year: string; revenue: number | null; op_profit: number | null; opm: number | null; pat: number | null }[];
    ttm: { revenue: number | null; pat: number | null } | null;
    revenue_cagr_3y: number | null;
    revenue_cagr_5y: number | null;
    pat_cagr_3y: number | null;
    pat_cagr_5y: number | null;
  };
  balance: {
    lender: boolean;
    years: { year: string; equity: number | null; debt: number | null; deposits: number | null; de: number | null }[];
    working_capital: {
      year: string;
      debtor_days: number | null;
      inventory_days: number | null;
      payable_days: number | null;
      ccc: number | null;
      roce: number | null;
      roe: number | null;
    }[];
  };
  cash: {
    years: { year: string; cfo: number | null; pat: number | null; fcf: number | null; conversion: number | null }[];
    conversion_5y: number | null;
    fcf_5y: number | null;
  };
  flags: Flag[];
};

export type Numbers = {
  symbol: string;
  name: string;
  basis: string;
  sector: string[];
  ratios: Record<string, string>;
  metrics: Metrics;
  documents: {
    annual_reports: { year: string; url: string }[];
    concalls: { period: string; transcript: string | null; ppt: string | null }[];
  };
  source_url: string;
  market: Market | null;
};

export type Analysis = {
  one_liner: string;
  business: string;
  segments: { name: string; share_pct: number | null; note: string }[];
  promises: { made_in: string; metric: string; promised: string; delivered: string; verdict: string }[];
  narrative_vs_numbers: { claim: string; numbers_say: string; verdict: string }[];
  current_guidance: string[];
  bull: string[];
  bear: string[];
  watch: string[];
};

export type ResearchResult = {
  analysis: Analysis;
  calls: { period: string; url: string; tone: string; analyst_concerns: string[] }[];
  sources: {
    annual_report: { year: string; url: string } | null;
    transcripts: { period: string; url: string }[];
    ar_pages: number[];
  };
  usage: Usage;
  generated_at: string;
};

export type Step = { label: string; state: "pending" | "running" | "done" | "error" };

export type ResearchJob = {
  status: "idle" | "running" | "done" | "error" | "no_ai";
  steps?: Step[];
  result?: ResearchResult;
  error?: string;
};

export type AskAnswer = {
  answer: string;
  confidence: string;
  sources: { source: string; page: number }[];
  usage?: Usage;
};
