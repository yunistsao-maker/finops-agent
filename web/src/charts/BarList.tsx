import { useState } from 'react';
import { money } from '../lib/format';
import { Legend } from './Legend';
import { Tooltip, type TipRow } from './Tooltip';
import { useWidth } from './useWidth';

export interface BarSegment {
  key: string;
  label: string;
  value: number;
  color: string;
}
export interface BarRow {
  key: string;
  label: string;
  value: number;
  /** shown after the value, e.g. a share */
  note?: string;
  color?: string;
  /** stacked parts of the bar (e.g. token types); they should sum to value */
  segments?: BarSegment[];
}

/** Horizontal bars from one baseline, value at the tip. One color unless the rows carry identity colors. */
export function BarList({ rows, currency = 'USD', legend }: {
  rows: BarRow[]; currency?: string; legend?: { key: string; label: string; color: string }[];
}) {
  const [ref, width] = useWidth<HTMLDivElement>();
  const [tip, setTip] = useState<{ i: number; y: number; x: number } | null>(null);
  const max = Math.max(0, ...rows.map(r => r.value)) || 1;
  const row = tip ? rows[tip.i] : null;

  const tipRows: TipRow[] = row?.segments
    ? row.segments.filter(s => s.value).map(s => ({ key: s.key, label: s.label, value: money(s.value, currency), color: s.color }))
    : [];

  return (
    <div className="chart" ref={ref} onPointerLeave={() => setTip(null)}>
      <ul className="barlist">
        {rows.map((r, i) => {
          const w = `${Math.max(0, (r.value / max) * 100)}%`;
          const show = (e: { clientX: number; clientY: number; currentTarget: HTMLElement }) => {
            const box = ref.current!.getBoundingClientRect();
            setTip({ i, x: e.clientX - box.left, y: e.currentTarget.offsetTop + 22 });
          };
          return (
            <li key={r.key} tabIndex={0} className={tip && tip.i !== i ? 'dim' : ''}
                onPointerMove={show}
                onFocus={e => setTip({ i, x: 120, y: e.currentTarget.offsetTop + 22 })} onBlur={() => setTip(null)}>
              <span className="barlist-label" title={r.label}>{r.label}</span>
              <span className="barlist-track">
                <span className="barlist-bar" style={{ width: w }}>
                  {/* bars too short for readable segments are drawn whole (neutral); tooltip and table keep the split */}
                  {r.segments && r.value >= max * 0.08
                    ? r.segments.filter(s => s.value > 0).map(s => (
                        <span key={s.key} className="barlist-seg" style={{ flexGrow: s.value, background: s.color }} />
                      ))
                    : <span className="barlist-seg" style={{ flexGrow: 1, background: r.segments ? 'var(--series-other)' : (r.color ?? 'var(--series-1)') }} />}
                </span>
                <span className="barlist-value">
                  {money(r.value, currency)}{r.note && <span className="muted"> {r.note}</span>}
                </span>
              </span>
            </li>
          );
        })}
      </ul>
      {row && tip && (tipRows.length > 0 || row.note) && (
        <Tooltip x={tip.x} y={tip.y} width={width} title={row.label}
                 total={money(row.value, currency) + (row.note ? ` · ${row.note}` : '')} rows={tipRows} />
      )}
      {legend && <Legend items={legend} />}
    </div>
  );
}
