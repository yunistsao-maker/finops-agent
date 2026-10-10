import { money, pct } from '../lib/format';

export interface DeltaRow {
  key: string;
  label: string;
  delta: number;
  baseline: number;
  current: number;
  deltaPct: number | null;
}

/** Diverging bars around a zero line: increases to the right (red pole), decreases to the left (blue pole). */
export function DeltaBars({ rows, currency = 'USD' }: { rows: DeltaRow[]; currency?: string }) {
  const max = Math.max(0, ...rows.map(r => Math.abs(r.delta))) || 1;
  return (
    <ul className="deltabars">
      {rows.map(r => {
        const w = `${(Math.abs(r.delta) / max) * 50}%`;
        const up = r.delta >= 0;
        return (
          <li key={r.key} tabIndex={0}
              title={`${r.label}: ${money(r.baseline, currency)} → ${money(r.current, currency)}`}>
            <span className="barlist-label" title={r.label}>{r.label}</span>
            <span className="delta-track">
              <span className="delta-zero" />
              <span className={`delta-bar ${up ? 'up' : 'down'}`} style={up ? { left: '50%', width: w } : { right: '50%', width: w }} />
            </span>
            <span className="delta-value">
              {up ? '+' : ''}{money(r.delta, currency)}
              <span className="muted"> {r.deltaPct == null ? 'new' : `${up ? '+' : ''}${pct(r.deltaPct, 0)}`}</span>
            </span>
          </li>
        );
      })}
    </ul>
  );
}
