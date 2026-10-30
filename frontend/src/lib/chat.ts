"use client";

import type { AskAnswer } from "@/lib/api";
import { readLS } from "@/lib/api";

export type ChatMsg =
  | { role: "user"; text: string; at: number }
  | { role: "assistant"; text: string; at: number; answer?: AskAnswer; error?: boolean };

const KEY = "finn.chat.v1";
const MAX_PER_COMPANY = 30;

/** Saved conversations, one per company. */
export function loadChats(): Record<string, ChatMsg[]> {
  try {
    return JSON.parse(readLS(KEY) ?? "{}");
  } catch {
    return {};
  }
}

export function saveChats(chats: Record<string, ChatMsg[]>) {
  try {
    const trimmed = Object.fromEntries(Object.entries(chats).map(([k, v]) => [k, v.slice(-MAX_PER_COMPANY)]));
    localStorage.setItem(KEY, JSON.stringify(trimmed));
  } catch {}
}

/** Open the chat from anywhere, optionally about a company and with a question to send. */
export function openChat(detail: { symbol?: string; name?: string; question?: string } = {}) {
  window.dispatchEvent(new CustomEvent("finn:chat", { detail }));
}
