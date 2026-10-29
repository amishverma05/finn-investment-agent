export function Sparkline({
  data,
  width = 72,
  height = 24,
  className,
}: {
  data: number[];
  width?: number;
  height?: number;
  className?: string;
}) {
  if (!data || data.length < 2) return null;
  const min = Math.min(...data);
  const max = Math.max(...data);
  const range = max - min || 1;
  const step = width / (data.length - 1);
  const pts = data.map((v, i) => [i * step, height - ((v - min) / range) * (height - 2) - 1]);
  const d = pts.map(([x, y], i) => `${i ? "L" : "M"}${x.toFixed(1)},${y.toFixed(1)}`).join(" ");
  const up = data[data.length - 1] >= data[0];
  return (
    <svg width={width} height={height} viewBox={`0 0 ${width} ${height}`} className={className} aria-hidden>
      <path d={d} fill="none" strokeWidth={1.4} className={up ? "stroke-up" : "stroke-down"} strokeLinecap="round" />
    </svg>
  );
}

/** Small bar chart for quarterly/annual series. Negative values drop below the baseline. */
export function Bars({
  data,
  labels,
  height = 64,
  highlightLast = true,
}: {
  data: (number | null)[];
  labels?: string[];
  height?: number;
  highlightLast?: boolean;
}) {
  const vals = data.map((v) => v ?? 0);
  const max = Math.max(...vals.map(Math.abs), 1);
  return (
    <div>
      <div className="flex items-end gap-1" style={{ height }}>
        {vals.map((v, i) => (
          <div key={i} className="flex h-full flex-1 flex-col justify-end">
            <div
              className={
                v < 0
                  ? "rounded-sm bg-down/70"
                  : highlightLast && i === vals.length - 1
                    ? "rounded-sm bg-foreground"
                    : "rounded-sm bg-foreground/25"
              }
              style={{ height: `${Math.max(2, (Math.abs(v) / max) * height)}px` }}
            />
          </div>
        ))}
      </div>
      {labels && (
        <div className="mt-1.5 flex gap-1">
          {labels.map((l, i) => (
            <div key={i} className="flex-1 truncate text-center text-[9px] text-faint">
              {l}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
