import { cn } from "@/lib/utils";

export function Header({ eyebrow, title, right }: { eyebrow?: string; title: string; right?: React.ReactNode }) {
  return (
    <header className="flex items-end justify-between gap-3 px-4 pt-[max(env(safe-area-inset-top),1.25rem)] pb-4">
      <div>
        {eyebrow && <p className="text-xs text-muted-foreground">{eyebrow}</p>}
        <h1 className="font-serif text-[34px] leading-none tracking-tight">{title}</h1>
      </div>
      {right}
    </header>
  );
}

export function SectionTitle({ children, count, right }: { children: React.ReactNode; count?: number; right?: React.ReactNode }) {
  return (
    <div className="flex items-center justify-between px-4 pt-6 pb-2">
      <h2 className="text-[11px] font-medium tracking-[0.12em] text-muted-foreground uppercase">
        {children}
        {count != null && <span className="ml-1.5 text-faint">{count}</span>}
      </h2>
      {right}
    </div>
  );
}

export function Chip({
  active,
  onClick,
  children,
  className,
}: {
  active?: boolean;
  onClick?: () => void;
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <button
      onClick={onClick}
      className={cn(
        "shrink-0 rounded-full border px-3 py-1 text-xs transition-colors",
        active ? "border-foreground bg-foreground text-background" : "border-border text-muted-foreground hover:text-foreground",
        className,
      )}
    >
      {children}
    </button>
  );
}

export function Skeleton({ className }: { className?: string }) {
  return <div className={cn("animate-pulse rounded-lg bg-raised", className)} />;
}

export function ErrorNote({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return (
    <div className="mx-4 rounded-xl border border-down/30 bg-down/5 p-4 text-sm">
      <p className="text-down">{message}</p>
      {onRetry && (
        <button onClick={onRetry} className="mt-2 text-xs text-muted-foreground underline underline-offset-4">
          Try again
        </button>
      )}
    </div>
  );
}

export function Card({ className, children }: { className?: string; children: React.ReactNode }) {
  return <div className={cn("rounded-2xl border border-border bg-card", className)}>{children}</div>;
}
