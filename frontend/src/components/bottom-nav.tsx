"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { Bell, Briefcase, Newspaper, Search } from "lucide-react";
import { useNavCounter } from "@/lib/nav";
import { cn } from "@/lib/utils";

const TABS = [
  { href: "/", label: "Brief", icon: Newspaper },
  { href: "/alerts", label: "Alerts", icon: Bell },
  { href: "/research", label: "Research", icon: Search },
  { href: "/portfolio", label: "Portfolio", icon: Briefcase },
];

export function BottomNav() {
  const path = usePathname();
  useNavCounter(path);
  return (
    <nav className="fixed inset-x-0 bottom-0 z-40">
      <div className="mx-auto max-w-[480px] border-t border-border bg-background/85 backdrop-blur-xl pb-safe">
        <ul className="grid grid-cols-4">
          {TABS.map(({ href, label, icon: Icon }) => {
            const active = href === "/" ? path === "/" : path.startsWith(href);
            return (
              <li key={href}>
                <Link
                  href={href}
                  className={cn(
                    "flex flex-col items-center gap-1 pt-2.5 pb-1 text-[11px] transition-colors",
                    active ? "text-foreground" : "text-faint hover:text-muted-foreground",
                  )}
                >
                  <Icon className="size-5" strokeWidth={active ? 2.2 : 1.7} />
                  {label}
                </Link>
              </li>
            );
          })}
        </ul>
      </div>
    </nav>
  );
}
