"use client";

import { api } from "@/lib/api";

/*
 * Push notifications: register the service worker, ask permission, subscribe this device
 * with the browser's push service, and give the subscription to our backend.
 */

export type PushState =
  | "unsupported" // browser can't do push (or the page isn't on https/localhost)
  | "ios-install" // iPhone/iPad in Safari: needs "Add to Home Screen" first
  | "blocked" // the user said no; only browser settings can undo that
  | "off"
  | "on";

function isIOS() {
  return /iPad|iPhone|iPod/.test(navigator.userAgent) || (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1);
}

function isStandalone() {
  return window.matchMedia("(display-mode: standalone)").matches || (navigator as Navigator & { standalone?: boolean }).standalone === true;
}

function supported() {
  return typeof window !== "undefined" && window.isSecureContext && "serviceWorker" in navigator && "PushManager" in window && "Notification" in window;
}

async function registration() {
  return navigator.serviceWorker.register("/sw.js");
}

export async function getPushState(): Promise<PushState> {
  if (typeof window === "undefined") return "unsupported";
  if (isIOS() && !isStandalone()) return "ios-install";
  if (!supported()) return "unsupported";
  if (Notification.permission === "denied") return "blocked";
  const reg = await navigator.serviceWorker.getRegistration("/");
  const sub = await reg?.pushManager.getSubscription();
  return sub && Notification.permission === "granted" ? "on" : "off";
}

function keyToBytes(base64url: string) {
  const pad = "=".repeat((4 - (base64url.length % 4)) % 4);
  const raw = atob((base64url + pad).replace(/-/g, "+").replace(/_/g, "/"));
  return Uint8Array.from(raw, (c) => c.charCodeAt(0));
}

export async function enablePush(): Promise<PushState> {
  if (!supported()) return "unsupported";
  const permission = await Notification.requestPermission();
  if (permission !== "granted") return permission === "denied" ? "blocked" : "off";
  const reg = await registration();
  await navigator.serviceWorker.ready;
  const { public_key } = await api<{ public_key: string }>("/push/key");
  let sub = await reg.pushManager.getSubscription();
  if (sub) {
    // The server's key may have changed (e.g. its database was reset): subscribe again.
    const current = sub.options.applicationServerKey;
    const same = current && btoa(String.fromCharCode(...new Uint8Array(current))) === btoa(String.fromCharCode(...keyToBytes(public_key)));
    if (!same) {
      await sub.unsubscribe();
      sub = null;
    }
  }
  sub ??= await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: keyToBytes(public_key) });
  await api("/push/subscribe", { method: "POST", body: JSON.stringify(sub.toJSON()) });
  return "on";
}

export async function disablePush(): Promise<PushState> {
  const reg = await navigator.serviceWorker.getRegistration("/");
  const sub = await reg?.pushManager.getSubscription();
  if (sub) {
    await api("/push/unsubscribe", { method: "POST", body: JSON.stringify({ endpoint: sub.endpoint }) }).catch(() => {});
    await sub.unsubscribe();
  }
  return "off";
}

export async function sendTestPush() {
  const reg = await navigator.serviceWorker.getRegistration("/");
  const sub = await reg?.pushManager.getSubscription();
  if (!sub) throw new Error("Turn on notifications first.");
  await api("/push/test", { method: "POST", body: JSON.stringify({ endpoint: sub.endpoint }) });
}
