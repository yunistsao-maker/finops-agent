import type { ReactNode } from 'react';

export interface TipRow {
  key: string;
  label: string;
  value: string;
  color?: string;
}

/** One tooltip: the value leads, the series name follows, keyed by a short line of the series color. */
export function Tooltip({ x, y, width, title, total, rows }: {
  x: number; y: number; width: number; title: ReactNode; total?: string; rows: TipRow[];
}) {
  const w = 220;
  // right of the pointer, or left of it when that would run past the edge
  const left = Math.max(0, x + 14 + w > width ? x - 14 - w : x + 14);
  return (
    <div className="tooltip" style={{ left, top: Math.max(0, y), width: w }} role="status">
      <div className="tooltip-title">{title}</div>
      {total && <div className="tooltip-total">{total}</div>}
      {rows.map(r => (
        <div className="tooltip-row" key={r.key}>
          {r.color && <span className="line-key" style={{ background: r.color }} />}
          <span className="tooltip-value">{r.value}</span>
          <span className="tooltip-label">{r.label}</span>
        </div>
      ))}
    </div>
  );
}
