/** Render [source] tags (e.g. [AR 2026 p.34]) as quiet chips. */
export function Cite({ text }: { text: string }) {
  const parts = text.split(/(\[[^\]]+\])/g);
  return (
    <>
      {parts.map((p, i) =>
        /^\[[^\]]+\]$/.test(p) ? (
          <span key={i} className="mx-0.5 inline-block rounded bg-foreground/[0.06] px-1 align-middle font-sans text-[10px] leading-4 text-faint">
            {p.slice(1, -1)}
          </span>
        ) : (
          <span key={i}>{p}</span>
        ),
      )}
    </>
  );
}
