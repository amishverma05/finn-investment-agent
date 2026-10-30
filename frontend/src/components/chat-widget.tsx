"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { usePathname } from "next/navigation";
import { AnimatePresence, motion } from "framer-motion";
import { ArrowUp, ChevronDown, MessageCircle, RotateCcw, Search, Sparkles, X } from "lucide-react";
import { api, readLS, useApi, useSearch, useStored, type AskAnswer, type Holding } from "@/lib/api";
import { loadChats, saveChats, type ChatMsg } from "@/lib/chat";
import { Cite } from "@/components/cite";
import { cn } from "@/lib/utils";

type Company = { symbol: string; name?: string };

const STARTERS = [
  "What did management promise last quarter?",
  "What is the capex plan?",
  "Why did margins change?",
  "What are analysts worried about?",
];

function readPicked(): (Company & { path: string }) | null {
  try {
    return JSON.parse(readLS("finn.chat.company") ?? "null");
  } catch {
    return null;
  }
}

/**
 * Floating "Ask FINN" assistant, available on every screen (bottom-left).
 * Answers come only from the company's annual report and earnings calls.
 * On a company's research page it talks about that company; elsewhere you pick one.
 */
export function ChatWidget() {
  const pathname = usePathname();
  const routeSymbol = useMemo(() => {
    const m = pathname.match(/^\/research\/([^/]+)$/);
    return m ? decodeURIComponent(m[1]).toUpperCase() : null;
  }, [pathname]);

  const [open, setOpen] = useState(false);
  const [picked, setPicked] = useState<(Company & { path: string }) | null>(readPicked);
  const [picking, setPicking] = useState(false);
  const [chats, setChats] = useState<Record<string, ChatMsg[]>>(loadChats);
  const chatsRef = useRef(chats);
  const [busy, setBusy] = useState(false);
  const busyRef = useRef(false);
  const [input, setInput] = useState("");

  // Which company we're talking about: one you picked on this screen, else the page's company,
  // else the last one you picked anywhere.
  const company: Company | null =
    picked && picked.path === pathname ? picked : routeSymbol ? { symbol: routeSymbol } : picked;
  const messages = company ? (chats[company.symbol] ?? []) : [];
  const showPicker = picking || !company;

  function choose(c: Company) {
    const p = { ...c, path: pathname };
    setPicked(p);
    setPicking(false);
    try {
      localStorage.setItem("finn.chat.company", JSON.stringify(p));
    } catch {}
  }

  function update(symbol: string, fn: (msgs: ChatMsg[]) => ChatMsg[]) {
    const next = { ...chatsRef.current, [symbol]: fn(chatsRef.current[symbol] ?? []) };
    chatsRef.current = next;
    setChats(next);
    saveChats(next);
  }

  async function send(text: string, co: Company | null = company) {
    const question = text.trim();
    if (!question || !co || busyRef.current) return;
    busyRef.current = true;
    setBusy(true);
    setInput("");
    const history = (chatsRef.current[co.symbol] ?? [])
      .filter((m) => !(m.role === "assistant" && m.error))
      .slice(-6)
      .map((m) => ({ role: m.role, text: m.text }));
    update(co.symbol, (m) => [...m, { role: "user", text: question, at: Date.now() }]);
    try {
      const a = await api<AskAnswer>(`/research/${co.symbol}/ask`, {
        method: "POST",
        body: JSON.stringify({ question, history }),
      });
      update(co.symbol, (m) => [...m, { role: "assistant", text: a.answer, answer: a, at: Date.now() }]);
    } catch (e) {
      const msg = e instanceof Error ? e.message : "Something went wrong";
      update(co.symbol, (m) => [...m, { role: "assistant", text: `Couldn't answer: ${msg}`, error: true, at: Date.now() }]);
    } finally {
      busyRef.current = false;
      setBusy(false);
    }
  }

  // Let other parts of the app open the chat (e.g. "Ask the filings" on a research page).
  const sendRef = useRef(send);
  useEffect(() => {
    sendRef.current = send;
  });
  useEffect(() => {
    const onOpen = (e: Event) => {
      const d = (e as CustomEvent<{ symbol?: string; name?: string; question?: string }>).detail ?? {};
      setOpen(true);
      if (d.symbol) {
        const co = { symbol: d.symbol.toUpperCase(), name: d.name };
        const p = { ...co, path: window.location.pathname };
        setPicked(p);
        setPicking(false);
        if (d.question) sendRef.current(d.question, co);
      }
    };
    window.addEventListener("finn:chat", onOpen);
    return () => window.removeEventListener("finn:chat", onOpen);
  }, []);

  // Escape closes; on phones the page behind shouldn't scroll while the chat is open.
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    window.addEventListener("keydown", onKey);
    const phone = window.matchMedia("(max-width: 639px)").matches;
    const prev = document.body.style.overflow;
    if (phone) document.body.style.overflow = "hidden";
    return () => {
      window.removeEventListener("keydown", onKey);
      document.body.style.overflow = prev;
    };
  }, [open]);

  // Keep the newest message in view.
  const endRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (open) endRef.current?.scrollIntoView({ block: "end" });
  }, [open, messages.length, busy, showPicker]);

  return (
    <>
      {/* Launcher: bottom-left, above the tab bar, aligned to the app column */}
      <motion.button
        onClick={() => setOpen((o) => !o)}
        aria-label={open ? "Close assistant" : "Ask FINN"}
        whileTap={{ scale: 0.92 }}
        className={cn(
          "fixed z-50 flex size-13 items-center justify-center rounded-full bg-foreground text-background shadow-[0_8px_30px_rgb(0_0_0/0.5)]",
          "bottom-[calc(env(safe-area-inset-bottom)+5rem)] left-[max(1rem,calc(50%-240px+1rem))]",
          open && "max-sm:hidden",
        )}
      >
        <AnimatePresence initial={false} mode="wait">
          <motion.span
            key={open ? "x" : "chat"}
            initial={{ rotate: -45, opacity: 0, scale: 0.6 }}
            animate={{ rotate: 0, opacity: 1, scale: 1 }}
            exit={{ rotate: 45, opacity: 0, scale: 0.6 }}
            transition={{ duration: 0.15 }}
          >
            {open ? <X className="size-5" /> : <MessageCircle className="size-5.5" />}
          </motion.span>
        </AnimatePresence>
      </motion.button>

      <AnimatePresence>
        {open && (
          <motion.div
            role="dialog"
            aria-label="Ask FINN"
            initial={{ opacity: 0, y: 24, scale: 0.97 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: 24, scale: 0.97 }}
            transition={{ duration: 0.2, ease: [0.2, 0.8, 0.2, 1] }}
            style={{ transformOrigin: "bottom left" }}
            className={cn(
              "fixed z-[60] flex flex-col overflow-hidden bg-background",
              // phones: full screen, like Intercom's mobile messenger
              "inset-0",
              // larger screens: a floating panel above the launcher
              "sm:inset-auto sm:bottom-[calc(env(safe-area-inset-bottom)+9.25rem)] sm:left-[max(1rem,calc(50%-240px+1rem))]",
              "sm:h-[min(640px,calc(100dvh-11rem))] sm:w-[380px] sm:rounded-2xl sm:border sm:border-border sm:shadow-[0_20px_60px_rgb(0_0_0/0.6)]",
            )}
          >
            {/* Header */}
            <div className="border-b border-border bg-card px-4 pt-[max(env(safe-area-inset-top),0.875rem)] pb-3 sm:pt-3.5">
              <div className="flex items-center gap-3">
                <div className="flex size-9 shrink-0 items-center justify-center rounded-full bg-foreground text-background">
                  <Sparkles className="size-4" />
                </div>
                <div className="min-w-0 flex-1">
                  <p className="text-[15px] font-medium leading-tight">Ask FINN</p>
                  <p className="text-[11px] text-muted-foreground">Answers only from annual reports & earnings calls</p>
                </div>
                {company && messages.length > 0 && !showPicker && (
                  <button
                    onClick={() => update(company.symbol, () => [])}
                    className="rounded-full p-1.5 text-muted-foreground hover:text-foreground"
                    aria-label="New conversation"
                    title="New conversation"
                  >
                    <RotateCcw className="size-4" />
                  </button>
                )}
                <button onClick={() => setOpen(false)} className="rounded-full p-1.5 text-muted-foreground hover:text-foreground" aria-label="Close">
                  <X className="size-5" />
                </button>
              </div>
              {company && (
                <button
                  onClick={() => setPicking((p) => !p)}
                  className="mt-2.5 inline-flex items-center gap-1.5 rounded-full border border-border bg-background px-2.5 py-1 text-xs"
                >
                  <span className="text-muted-foreground">About</span>
                  <span className="num font-medium">{company.symbol}</span>
                  {company.name && <span className="max-w-[150px] truncate text-muted-foreground">{company.name}</span>}
                  <ChevronDown className={cn("size-3.5 text-muted-foreground transition-transform", picking && "rotate-180")} />
                </button>
              )}
            </div>

            {/* Body */}
            <div className="min-h-0 flex-1 overflow-y-auto px-4 py-4">
              {showPicker ? (
                <CompanyPicker onPick={choose} />
              ) : (
                <div className="space-y-3">
                  <Bubble role="assistant">
                    Hi! Ask me anything about <span className="font-medium">{company?.name ?? company?.symbol}</span>. I read its
                    latest annual report and last 4 earnings calls, and cite the page for every claim.
                  </Bubble>
                  {messages.length === 0 && (
                    <div className="flex flex-col items-start gap-1.5 pl-1">
                      {STARTERS.map((s) => (
                        <button
                          key={s}
                          onClick={() => send(s)}
                          className="rounded-full border border-border px-3 py-1.5 text-left text-[13px] text-muted-foreground hover:border-foreground/25 hover:text-foreground"
                        >
                          {s}
                        </button>
                      ))}
                    </div>
                  )}
                  {messages.map((m, i) =>
                    m.role === "user" ? (
                      <Bubble key={i} role="user">
                        {m.text}
                      </Bubble>
                    ) : (
                      <Bubble key={i} role="assistant" error={m.error}>
                        <Cite text={m.text} />
                        {m.answer && (m.answer.sources.length > 0 || m.answer.confidence) && (
                          <div className="mt-2 flex flex-wrap items-center gap-1 border-t border-border pt-2 text-[10px] text-faint">
                            <span className="mr-0.5">confidence: {m.answer.confidence}</span>
                            {m.answer.sources.map((s) => (
                              <span key={`${s.source}-${s.page}`} className="rounded bg-foreground/[0.06] px-1">
                                {s.source} p.{s.page}
                              </span>
                            ))}
                          </div>
                        )}
                      </Bubble>
                    ),
                  )}
                  {busy && <Typing />}
                </div>
              )}
              <div ref={endRef} />
            </div>

            {/* Composer */}
            {!showPicker && (
              <form
                onSubmit={(e) => {
                  e.preventDefault();
                  send(input);
                }}
                className="border-t border-border bg-card px-3 pt-2.5 pb-[max(env(safe-area-inset-bottom),0.625rem)] sm:pb-2.5"
              >
                <div className="flex items-end gap-2 rounded-2xl border border-border bg-background py-1.5 pr-1.5 pl-3.5 focus-within:border-ring">
                  <textarea
                    value={input}
                    onChange={(e) => setInput(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter" && !e.shiftKey) {
                        e.preventDefault();
                        send(input);
                      }
                    }}
                    rows={1}
                    placeholder={company ? `Ask about ${company.symbol}…` : "Ask a question…"}
                    className="max-h-28 min-h-[30px] flex-1 resize-none bg-transparent py-1 text-[15px] leading-snug outline-none [field-sizing:content] placeholder:text-faint"
                  />
                  <button
                    type="submit"
                    disabled={busy || !input.trim()}
                    aria-label="Send"
                    className="flex size-8 shrink-0 items-center justify-center rounded-full bg-foreground text-background transition-opacity disabled:opacity-25"
                  >
                    <ArrowUp className="size-4" />
                  </button>
                </div>
                <p className="mt-1.5 text-center text-[10px] text-faint">AI answers can be wrong. Check the cited pages.</p>
              </form>
            )}
          </motion.div>
        )}
      </AnimatePresence>
    </>
  );
}

function Bubble({ role, error, children }: { role: "user" | "assistant"; error?: boolean; children: React.ReactNode }) {
  return (
    <motion.div
      initial={{ opacity: 0, y: 6 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.15 }}
      className={cn("flex", role === "user" ? "justify-end" : "justify-start")}
    >
      <div
        className={cn(
          "max-w-[85%] px-3.5 py-2.5 text-[14px] leading-relaxed",
          role === "user"
            ? "rounded-2xl rounded-br-md bg-foreground text-background"
            : "rounded-2xl rounded-bl-md bg-raised",
          error && "text-down",
        )}
      >
        {children}
      </div>
    </motion.div>
  );
}

function Typing() {
  return (
    <div className="flex flex-col items-start gap-1">
      <div className="flex items-center gap-1 rounded-2xl rounded-bl-md bg-raised px-3.5 py-3">
        {[0, 1, 2].map((i) => (
          <motion.span
            key={i}
            className="size-1.5 rounded-full bg-muted-foreground"
            animate={{ opacity: [0.3, 1, 0.3] }}
            transition={{ duration: 1, repeat: Infinity, delay: i * 0.18 }}
          />
        ))}
      </div>
      <p className="pl-1 text-[11px] text-faint">Searching the annual report and earnings calls… the first question can take ~20s.</p>
    </div>
  );
}

function CompanyPicker({ onPick }: { onPick: (c: Company) => void }) {
  const [q, setQ] = useState("");
  const hits = useSearch(q);
  const book = useApi<{ items: Holding[] }>("/portfolio");
  const stored = useStored("finn.recent");
  const recent = useMemo<string[]>(() => {
    try {
      return JSON.parse(stored ?? "[]");
    } catch {
      return [];
    }
  }, [stored]);
  const holdings = book.data?.items ?? [];

  return (
    <div>
      <p className="mb-3 text-[14px]">Which company do you want to ask about?</p>
      <div className="flex items-center gap-2 rounded-xl border border-border bg-card px-3 py-2.5 focus-within:border-ring">
        <Search className="size-4 text-muted-foreground" />
        <input
          autoFocus
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder="Any NSE company or ticker"
          className="w-full bg-transparent text-[14px] outline-none placeholder:text-faint"
        />
      </div>
      {hits.length > 0 ? (
        <ul className="mt-2 divide-y divide-border overflow-hidden rounded-xl border border-border bg-card">
          {hits.map((h) => (
            <li key={h.symbol}>
              <button onClick={() => onPick(h)} className="flex w-full items-center justify-between px-3 py-2.5 text-left active:bg-raised">
                <span className="truncate text-[14px]">{h.name}</span>
                <span className="num ml-3 text-xs text-muted-foreground">{h.symbol}</span>
              </button>
            </li>
          ))}
        </ul>
      ) : (
        <>
          {recent.length > 0 && (
            <>
              <p className="mt-4 mb-2 text-[10px] tracking-[0.12em] text-faint uppercase">Recently researched</p>
              <div className="flex flex-wrap gap-1.5">
                {recent.map((s) => (
                  <button key={s} onClick={() => onPick({ symbol: s })} className="num rounded-full border border-border px-2.5 py-1 text-xs">
                    {s}
                  </button>
                ))}
              </div>
            </>
          )}
          {holdings.length > 0 && (
            <>
              <p className="mt-4 mb-2 text-[10px] tracking-[0.12em] text-faint uppercase">Your portfolio</p>
              <div className="flex flex-wrap gap-1.5">
                {holdings.map((h) => (
                  <button
                    key={h.symbol}
                    onClick={() => onPick({ symbol: h.symbol, name: h.name })}
                    className="num rounded-full border border-border px-2.5 py-1 text-xs"
                  >
                    {h.symbol}
                  </button>
                ))}
              </div>
            </>
          )}
        </>
      )}
    </div>
  );
}
