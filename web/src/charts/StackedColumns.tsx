import { useState } from 'react';
import { label as labelOf, seriesColor } from '../lib/colors';
import { money, moneyCompact, niceTicks, shortDate } from '../lib/format';
import type { TrendDay } from '../lib/types';
import { Legend } from './Legend';
import { Tooltip } from './Tooltip';
import { useWidth } from './useWidth';

const M = { top: 22, right: 8, bottom: 24, left: 52 };
const GAP = 2; // surface gap between stacked segments

/** Path of a column segment with a 4px rounded data-end on top, square at the bottom. */
function topRounded(x: number, y: number, w: number, h: number): string {
  const r = Math.min(4, w / 2, h);
  return `M${x},${y + h}V${y + r}Q${x},${y} ${x + r},${y}H${x + w - r}Q${x + w},${y} ${x + w},${y + r}V${y + h}Z`;
}

/** Daily cost as stacked columns, one stack per day, series stacked in a fixed order (first at the baseline). */
export function StackedColumns({ days, series, dim, currency = 'USD', estimated = [], height = 240 }: {
  days: TrendDay[]; series: string[]; dim: string; currency?: string; estimated?: string[]; height?: number;
}) {
  const [ref, width] = useWidth<HTMLDivElement>();
  const [hover, setHover] = useState<number | null>(null);
  const keys = [...series, ...(days.some(d => d.by['(other)']) && !series.includes('(other)') ? ['(other)'] : [])];
  const est = new Set(estimated);

  const stackTotal = (d: TrendDay) => keys.reduce((s, k) => s + Math.max(0, d.by[k] ?? 0), 0);
  const ticks = niceTicks(Math.max(0, ...days.map(stackTotal)));
  const yMax = ticks[ticks.length - 1] || 1;
  const plotW = Math.max(10, width - M.left - M.right);
  const plotH = height - M.top - M.bottom;
  const slot = plotW / Math.max(1, days.length);
  const bw = Math.max(2, Math.min(24, slot * 0.7));
  const y = (v: number) => M.top + plotH - (v / yMax) * plotH;
  const labelEvery = Math.max(1, Math.ceil(days.length / Math.max(2, Math.floor(plotW / 64))));
  const peak = days.reduce((best, d, i) => (stackTotal(d) > stackTotal(days[best] ?? d) ? i : best), 0);
  const hd = hover != null ? days[hover] : null;

  return (
    <div className="chart" ref={ref}>
      <svg width={width} height={height} role="img" aria-label={`Daily cost by ${dim}`}
           onPointerLeave={() => setHover(null)}>
        {ticks.map(t => (
          <g key={t}>
            <line x1={M.left} x2={width - M.right} y1={y(t)} y2={y(t)} className={t === 0 ? 'baseline' : 'gridline'} />
            <text x={M.left - 8} y={y(t)} dy="0.32em" textAnchor="end" className="tick">{moneyCompact(t, currency)}</text>
          </g>
        ))}
        {days.map((d, i) => {
          const x = M.left + i * slot + (slot - bw) / 2;
          let acc = 0;
          const segs = keys.filter(k => (d.by[k] ?? 0) > 0);
          return (
            <g key={d.date} opacity={hover != null && hover !== i ? 0.45 : 1}>
              {segs.map((k, j) => {
                const v = d.by[k];
                const yTop = y(acc + v);
                const yBot = y(acc) - (j > 0 ? GAP : 0);
                acc += v;
                const h = Math.max(0, yBot - yTop);
                if (h <= 0) return null;
                return j === segs.length - 1
                  ? <path key={k} d={topRounded(x, yTop, bw, h)} fill={seriesColor(dim, k)} />
                  : <rect key={k} x={x} y={yTop} width={bw} height={h} fill={seriesColor(dim, k)} />;
              })}
              {i % labelEvery === 0 && (
                <text x={x + bw / 2} y={height - 6} textAnchor="middle" className="tick">{shortDate(d.date)}</text>
              )}
              {i === peak && stackTotal(d) > 0 && (
                <text x={x + bw / 2} y={y(stackTotal(d)) - 6} textAnchor="middle" className="direct-label">
                  {money(d.total, currency)}
                </text>
              )}
            </g>
          );
        })}
        {/* hit targets: the whole day band, bigger than the column */}
        {days.map((d, i) => (
          <rect key={d.date} x={M.left + i * slot} y={M.top} width={slot} height={plotH} fill="transparent"
                tabIndex={0} aria-label={`${shortDate(d.date)}: ${money(d.total, currency)}`}
                onPointerEnter={() => setHover(i)} onFocus={() => setHover(i)} onBlur={() => setHover(null)} />
        ))}
      </svg>
      {hd && hover != null && (
        <Tooltip
          x={M.left + hover * slot + slot / 2} y={M.top} width={width}
          title={<>{shortDate(hd.date)}{est.has(hd.date) && <span className="muted"> · estimated</span>}</>}
          total={money(hd.total, currency)}
          rows={[...keys].reverse().filter(k => hd.by[k]).map(k => ({
            key: k, label: labelOf(dim, k), value: money(hd.by[k], currency), color: seriesColor(dim, k),
          }))}
        />
      )}
      <Legend items={keys.map(k => ({ key: k, label: labelOf(dim, k), color: seriesColor(dim, k) }))} />
    </div>
  );
}
