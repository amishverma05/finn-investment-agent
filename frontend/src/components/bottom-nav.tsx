"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import { Bell, Briefcase, Newspaper, Search, X, Zap } from "lucide-react";
import { useApi, useStored, type Unread } from "@/lib/api";
import { useNavCounter } from "@/lib/nav";
import { cn } from "@/lib/utils";

const TABS = [
  { href: "/", label: "Brief", icon: Newspaper },
  { href: "/alerts", label: "Alerts", icon: Bell },
  { href: "/research", label: "Research", icon: Search },
  { href: "/portfolio", label: "Portfolio", icon: Briefcase },
];

const CHECK_EVERY = 60_000; // how often the app asks "anything new?" while open

/** New alerts since you last opened the Alerts screen (polled while the app is open). */
function useUnread() {
  const seen = Number(useStored("finn.alertsSeen")) || 0;
  const [tick, setTick] = useState(0);
  useEffect(() => {
    const bump = () => setTick((n) => n + 1);
    const timer = setInterval(bump, CHECK_EVERY);
    window.addEventListener("focus", bump);
    return () => {
      clearInterval(timer);
      window.removeEventListener("focus", bump);
    };
  }, []);
  return useApi<Unread>(`/alerts/unread?since=${seen}&t=${tick}`).data;
}

export function BottomNav() {
  const path = usePathname();
  const router = useRouter();
  useNavCounter(path);
  const unread = useUnread();
  const onAlerts = path.startsWith("/alerts");
  const badge = !onAlerts && unread ? unread.count : 0;

  // In-app banner: an alert that appeared while you're using the app (not ones from before you opened it).
  const [openedAt] = useState(() => Date.now());
  const [dismissed, setDismissed] = useState<string | null>(null);
  const latest = unread?.latest;
  const toastId = latest && latest.first_seen > openedAt && latest.id !== dismissed && !onAlerts ? latest.id : null;
  useEffect(() => {
    if (!toastId) return;
    const t = setTimeout(() => setDismissed(toastId), 9000);
    return () => clearTimeout(t);
  }, [toastId]);

  return (
    <>
      <AnimatePresence>
        {toastId && latest && (
          <motion.div
            key={toastId}
            initial={{ y: -80, opacity: 0 }}
            animate={{ y: 0, opacity: 1 }}
            exit={{ y: -80, opacity: 0 }}
            transition={{ type: "spring", stiffness: 380, damping: 32 }}
            className="fixed inset-x-0 top-[max(env(safe-area-inset-top),0.75rem)] z-[70] mx-auto w-[calc(100%-1.5rem)] max-w-[456px]"
          >
            <div className="flex items-start gap-3 rounded-2xl border border-border bg-card/95 p-3.5 shadow-[0_12px_40px_rgb(0_0_0/0.55)] backdrop-blur-xl">
              <button
                onClick={() => {
                  setDismissed(toastId);
                  router.push(latest.url);
                }}
                className="flex min-w-0 flex-1 items-start gap-3 text-left"
              >
                <span className="mt-0.5 flex size-8 shrink-0 items-center justify-center rounded-full bg-signal/15 text-signal">
                  <Zap className="size-4" />
                </span>
                <span className="min-w-0">
                  <span className="block text-[14px] font-medium">{latest.title}</span>
                  <span className="mt-0.5 line-clamp-2 block text-[13px] leading-snug text-muted-foreground">{latest.body}</span>
                  {unread.count > 1 && <span className="mt-1 block text-[11px] text-faint">+{unread.count - 1} more in Alerts</span>}
                </span>
              </button>
              <button onClick={() => setDismissed(toastId)} className="rounded-full p-1 text-faint hover:text-foreground" aria-label="Dismiss">
                <X className="size-4" />
              </button>
            </div>
          </motion.div>
        )}
      </AnimatePresence>

      <nav className="fixed inset-x-0 bottom-0 z-40">
        <div className="mx-auto max-w-[480px] border-t border-border bg-background/85 backdrop-blur-xl pb-safe">
          <ul className="grid grid-cols-4">
            {TABS.map(({ href, label, icon: Icon }) => {
              const active = href === "/" ? path === "/" : path.startsWith(href);
              const count = href === "/alerts" ? badge : 0;
              return (
                <li key={href}>
                  <Link
                    href={href}
                    className={cn(
                      "flex flex-col items-center gap-1 pt-2.5 pb-1 text-[11px] transition-colors",
                      active ? "text-foreground" : "text-faint hover:text-muted-foreground",
                    )}
                  >
                    <span className="relative">
                      <Icon className="size-5" strokeWidth={active ? 2.2 : 1.7} />
                      {count > 0 && (
                        <span className="num absolute -top-1.5 -right-2.5 min-w-[17px] rounded-full bg-signal px-1 text-center text-[10px] leading-[17px] font-semibold text-background">
                          {count > 9 ? "9+" : count}
                        </span>
                      )}
                    </span>
                    {label}
                  </Link>
                </li>
              );
            })}
          </ul>
        </div>
      </nav>
    </>
  );
}
