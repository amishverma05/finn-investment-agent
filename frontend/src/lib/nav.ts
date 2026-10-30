"use client";

import { useEffect, useRef } from "react";

/*
 * Going "back" should return you to the exact spot you left.
 *
 * - Each list screen saves its scroll position (per URL) in sessionStorage as you scroll.
 * - When you arrive via the browser/app back button (a popstate), the screen restores it
 *   as soon as its content has rendered.
 * - We also count in-app navigations so a Back arrow knows whether there is an in-app page
 *   to go back to (if you opened a link directly, there isn't).
 */

let lastPop = 0;
let inAppNavs = 0;

if (typeof window !== "undefined") {
  window.addEventListener("popstate", () => {
    lastPop = Date.now();
  });
}

const scrollKey = () => `finn.scroll:${window.location.pathname}${window.location.search}`;

/** Remember this screen's scroll position, and restore it after a back navigation once `ready`. */
export function useScrollMemory(ready: boolean) {
  useEffect(() => {
    const key = scrollKey();
    const onScroll = () => {
      try {
        sessionStorage.setItem(key, String(Math.round(window.scrollY)));
      } catch {}
    };
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => window.removeEventListener("scroll", onScroll);
  }, []);

  const restored = useRef(false);
  useEffect(() => {
    if (!ready || restored.current) return;
    restored.current = true;
    if (Date.now() - lastPop > 15_000) return; // not arriving via Back
    let y = 0;
    try {
      y = Number(sessionStorage.getItem(scrollKey())) || 0;
    } catch {}
    if (y > 0) requestAnimationFrame(() => window.scrollTo(0, y));
  }, [ready]);
}

/** Call once from a component that sees every route change (the bottom nav). */
export function useNavCounter(pathname: string) {
  useEffect(() => {
    inAppNavs += 1;
  }, [pathname]);
}

/** True if an earlier page of this app is in the history stack. */
export function canGoBack() {
  return inAppNavs > 1;
}
