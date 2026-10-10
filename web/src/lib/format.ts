const money2 = new Map<string, Intl.NumberFormat>();

function fmt(currency: string, digits: number): Intl.NumberFormat {
  const key = `${currency}:${digits}`;
  let f = money2.get(key);
  if (!f) {
    f = new Intl.NumberFormat('en-US', { style: 'currency', currency, minimumFractionDigits: digits, maximumFractionDigits: digits });
    money2.set(key, f);
  }
  return f;
}

/** Same rule as the agent: 2 decimals, 4 when the amount is below 1 (and not zero). */
export function money(v: number, currency = 'USD'): string {
  const a = Math.abs(v);
  return fmt(currency, a > 0 && a < 1 ? 4 : 2).format(v);
}

/** Compact money for axis ticks and tiles: $0, $25, $1.2K. */
export function moneyCompact(v: number, currency = 'USD'): string {
  return new Intl.NumberFormat('en-US', {
    style: 'currency', currency, notation: 'compact', maximumFractionDigits: Math.abs(v) < 10 && v !== 0 ? 1 : 0,
  }).format(v);
}

export const pct = (v: number | null | undefined, digits = 1) =>
  v == null ? '–' : `${(v * 100).toFixed(Math.abs(v) < 0.001 && v !== 0 ? 2 : digits)}%`;

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

/** "2026-10-07" -> "Oct 7" (dates are UTC calendar days; no time-zone conversion). */
export function shortDate(iso: string): string {
  const [, m, d] = iso.split('-').map(Number);
  return `${MONTHS[m - 1]} ${d}`;
}

export function dateRange(start: string, end: string): string {
  return start === end ? shortDate(start) : `${shortDate(start)} – ${shortDate(end)}, ${end.slice(0, 4)}`;
}

/** Up to ~5 clean ticks from 0 to a rounded max. */
export function niceTicks(max: number, count = 4): number[] {
  if (!(max > 0)) return [0, 1];
  const raw = max / count;
  const mag = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 2.5, 5, 10].map(s => s * mag).find(s => s >= raw) ?? 10 * mag;
  const ticks = [];
  for (let t = 0; t <= max + step * 0.999; t += step) ticks.push(Number(t.toFixed(10)));
  return ticks;
}
