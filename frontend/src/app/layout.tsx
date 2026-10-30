import { Suspense } from "react";
import type { Metadata, Viewport } from "next";
import { Geist, Geist_Mono, Instrument_Serif } from "next/font/google";
import { BottomNav } from "@/components/bottom-nav";
import { ChatWidget } from "@/components/chat-widget";
import "./globals.css";

const geistSans = Geist({
  variable: "--font-geist-sans",
  subsets: ["latin"],
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

const serif = Instrument_Serif({
  variable: "--font-serif",
  subsets: ["latin"],
  weight: "400",
});

export const metadata: Metadata = {
  title: "FINN",
  description: "Financial Investment Agent: what changed in your portfolio, and why it matters.",
  appleWebApp: { capable: true, title: "FINN", statusBarStyle: "black-translucent" },
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  viewportFit: "cover",
  themeColor: "#0b0b0c",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html
      lang="en"
      className={`${geistSans.variable} ${geistMono.variable} ${serif.variable} h-full antialiased`}
    >
      <body className="min-h-full">
        <div className="mx-auto min-h-dvh w-full max-w-[480px] border-x border-border/60 pb-24">
          {children}
        </div>
        <Suspense fallback={null}>
          <BottomNav />
          <ChatWidget />
        </Suspense>
      </body>
    </html>
  );
}
