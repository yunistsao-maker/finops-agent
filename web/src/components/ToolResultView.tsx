import { BarList, type BarRow } from '../charts/BarList';
import { DeltaBars } from '../charts/DeltaBars';
import { StackedColumns } from '../charts/StackedColumns';
import { CATEGORY_GROUP, GROUPS, label, seriesColor } from '../lib/colors';
import { dateRange, money, pct, shortDate } from '../lib/format';
import type {
  Anomalies, Breakdown, Compare, Dims, Llm, Plot, Projection, Projects, Summary, Trend, TrendDay,
} from '../lib/types';
import { ChartCard, DataTable } from './ChartCard';
import { ErrorBoundary } from './ErrorBoundary';
import { StatTile } from './StatTile';

type Obj = Record<string, unknown>;
const isObj = (v: unknown): v is Obj => !!v && typeof v === 'object' && !Array.isArray(v);
const isMeta = (v: Obj) => isObj(v.period) && typeof (v.period as Obj).start === 'string' && typeof v.currency === 'string';
const arr = (v: unknown) => Array.isArray(v);

/** Shape checks per tool: a result that does not match is shown as data instead of being charted (and crashing). */
const SHAPES: Record<string, (r: Obj) => boolean> = {
  cost_summary: r => isMeta(r) && arr(r.groupBy) && arr(r.rows),
  daily_trend: r => isMeta(r) && arr(r.series) && arr(r.days),
  llm_costs: r => isMeta(r) && arr(r.models),
  project_costs: r => isMeta(r) && arr(r.projects),
  compare_periods: r => isObj(r.totals) && arr(r.rows) && arr(r.groupBy) && isObj(r.baseline) && isMeta(r.baseline as Obj),
  find_anomalies: r => isMeta(r) && arr(r.anomalies),
  month_end_projection: r => typeof r.error === 'string' || (isMeta(r) && typeof r.monthToDate === 'number'),
  project_resources: r => isObj(r.projects),
};

const isPlot = (r: Obj): boolean => {
  const c = r.chart;
  if (!isObj(c) || typeof c.x !== 'string') return false;
  if (c.kind === 'stacked_columns') return SHAPES.daily_trend(r);
  if (c.kind === 'bars') return isMeta(r) && arr(r.rows);
  return false;
};

/**
 * Charts for a tool result; every number comes from finops.analysis, not from the model's text.
 * Never throws: errors and unexpected shapes are shown as text/data, and a rendering bug stays inside this card.
 */
export function ToolResultView({ name, status, result }: { name: string; status?: string; result: unknown }) {
  const err = toolError(status, result);
  if (err) return <p className="tool-error" role="status"><strong>{name}</strong> failed: {err}</p>;
  if (!isObj(result)) return null;
  return (
    <ErrorBoundary label={`the ${name} result`} fallback={e => <RawResult name={name} result={result} reason={e.message} />}>
      <ToolChart name={name} result={result} />
    </ErrorBoundary>
  );
}

function toolError(status: string | undefined, result: unknown): string | null {
  if (isObj(result) && result.error != null && !('monthToDate' in result)) return String(result.error);
  if (status === 'error') return isObj(result) && typeof result.text === 'string' ? result.text : 'the tool call failed';
  return null;
}

function ToolChart({ name, result }: { name: string; result: Obj }) {
  if (isPlot(result)) return <PlotView p={result as unknown as Plot} />;
  if (name === 'data_status') return null; // shown as a step only
  const shape = SHAPES[name];
  if (!shape) return <RawResult name={name} result={result} />;
  if (!shape(result)) return <RawResult name={name} result={result} reason="unexpected result shape" />;
  switch (name) {
    case 'cost_summary': return <SummaryView s={result as unknown as Summary} />;
    case 'daily_trend': return <TrendView t={result as unknown as Trend} />;
    case 'llm_costs': return <LlmView llm={result as unknown as Llm} />;
    case 'project_costs': return <ProjectsView p={result as unknown as Projects} />;
    case 'compare_periods': return <CompareView c={result as unknown as Compare} />;
    case 'find_anomalies': return <AnomaliesView a={result as unknown as Anomalies} />;
    case 'month_end_projection': return <ProjectionView p={result as unknown as Projection} />;
    case 'project_resources': return <ResourcesView r={result as unknown as ResourcesResult} />;
    default: return null;
  }
}

/** Fallback for results the UI has no chart for: a table when there are rows, otherwise the JSON, collapsed. */
function RawResult({ name, result, reason }: { name: string; result: Obj; reason?: string }) {
  const rows = Array.isArray(result.rows) ? (result.rows as unknown[]).filter(isObj).slice(0, 200) : [];
  const columns = rows.length ? [...new Set(rows.flatMap(r => Object.keys(r)))].filter(c => !isObj(rows[0][c])) : [];
  return (
    <section className="card raw-result">
      <p className="muted">{name}{reason ? ` · shown as data (${reason})` : ''}</p>
      {columns.length > 0
        ? <DataTable columns={columns} rows={rows.map(r => columns.map(c => (typeof r[c] === 'number' ? (r[c] as number) : String(r[c] ?? ''))))} />
        : (
          <details>
            <summary>Result</summary>
            <pre className="json">{JSON.stringify(result, null, 2).slice(0, 20000)}</pre>
          </details>
        )}
    </section>
  );
}

/** plot_cost: the chart spec says how to draw; the data comes from finops.analysis (trend or breakdown). */
export function PlotView({ p }: { p: Plot }) {
  const c = p.chart;
  if (c.kind === 'stacked_columns') {
    const t = p as unknown as Trend;
    return <TrendView t={t} title={c.title} />;
  }
  const b = p as unknown as Breakdown;
  const dim = c.series;
  const series = [...(b.series ?? []), ...(b.rows.some(r => r.by?.['(other)']) ? ['(other)'] : [])];
  const rows: BarRow[] = b.rows.map(r => ({
    key: r.key, label: label(c.x, r.key), value: r.cost, note: pct(r.share),
    color: dim ? undefined : seriesColor(c.x, r.key),
    segments: dim ? series.map(k => ({ key: k, label: label(dim, k), value: r.by?.[k] ?? 0, color: seriesColor(dim, k) })) : undefined,
  }));
  if (b.notShown?.count) rows.push({ key: '(rest)', label: `${b.notShown.count} more`, value: b.notShown.cost, color: 'var(--series-other)' });
  return (
    <ChartCard
      title={c.title}
      subtitle={`${dateRange(b.period.start, b.period.end)} · total ${money(b.total, b.currency)}${filtersText(b.filters)}`}
      table={{
        columns: [c.x, ...(dim ? series.map(k => label(dim, k)) : []), 'Cost', 'Share'],
        rows: b.rows.map(r => [label(c.x, r.key), ...(dim ? series.map(k => money(r.by?.[k] ?? 0, b.currency)) : []),
          money(r.cost, b.currency), pct(r.share)]),
      }}>
      <BarList rows={rows} currency={b.currency}
               legend={dim ? series.map(k => ({ key: k, label: label(dim, k), color: seriesColor(dim, k) })) : undefined} />
    </ChartCard>
  );
}

const dimLabel = (dims: string[], row: Dims) =>
  dims.map(d => label(d, String(row[d] ?? '-'))).join(' · ');

function filtersText(f: Record<string, string> | undefined) {
  const e = Object.entries(f ?? {});
  return e.length ? ` · ${e.map(([k, v]) => `${k}: ${v}`).join(', ')}` : '';
}

export function SummaryView({ s, title }: { s: Summary; title?: string }) {
  const dims = s.groupBy;
  const folded = s.seriesFolded?.count ? ` · ${s.seriesFolded.count} smaller series in "(other)"` : '';
  const sub = `${dateRange(s.period.start, s.period.end)} · total ${money(s.total, s.currency)}${filtersText(s.filters)}${folded}`;
  const table = {
    columns: [...dims, 'Cost', 'Share'],
    rows: s.rows.map(r => [...dims.map(d => label(d, String(r[d] ?? '-'))), money(r.cost, s.currency), pct(r.share)]),
  };
  const heading = title ?? `Cost by ${dims.join(' & ')}`;

  // date + one other dimension -> daily stacked columns
  if (dims.length === 2 && dims.includes('date')) {
    const other = dims.find(d => d !== 'date')!;
    const byDate = new Map<string, TrendDay>();
    const totals = new Map<string, number>();
    for (const r of s.rows) {
      const date = String(r.date), k = String(r[other] ?? '-');
      const day = byDate.get(date) ?? { date, total: 0, by: {} };
      day.by[k] = (day.by[k] ?? 0) + r.cost;
      day.total += r.cost;
      byDate.set(date, day);
      totals.set(k, (totals.get(k) ?? 0) + r.cost);
    }
    const series = [...totals].sort((a, b) => b[1] - a[1]).map(([k]) => k);
    return (
      <ChartCard title={heading} subtitle={sub} table={table}>
        <StackedColumns days={[...byDate.values()].sort((a, b) => a.date.localeCompare(b.date))}
                        series={orderSeries(other, series)} dim={other} currency={s.currency} estimated={s.estimatedDays} />
      </ChartCard>
    );
  }
  if (dims.length !== 1) return <ChartCard title={heading} subtitle={sub}><DataTable {...table} /></ChartCard>;

  const dim = dims[0];
  const identity = dim === 'group' || dim === 'category' || dim === 'token_type' || dim === 'provider';
  const rows: BarRow[] = s.rows.map(r => {
    const key = String(r[dim] ?? '-');
    return { key, label: label(dim, key), value: r.cost, note: pct(r.share), color: identity ? seriesColor(dim, key) : undefined };
  });
  if (s.notShown?.count) rows.push({ key: '(rest)', label: `${s.notShown.count} more`, value: s.notShown.cost, color: 'var(--series-other)' });
  const legend = dim === 'category'
    ? GROUPS.filter(g => s.rows.some(r => CATEGORY_GROUP[String(r.category)] === g))
        .map(g => ({ key: g, label: label('group', g), color: seriesColor('group', g) }))
    : undefined;
  return (
    <ChartCard title={heading} subtitle={sub} table={table}>
      <BarList rows={rows} currency={s.currency} legend={legend} />
    </ChartCard>
  );
}

/** Fixed dimensions keep their canonical order; others are ordered by size. */
function orderSeries(dim: string, series: string[]) {
  return dim === 'group' ? GROUPS.filter(g => series.includes(g)).concat(series.filter(s => !GROUPS.includes(s))) : series;
}

export function TrendView({ t, title }: { t: Trend; title?: string }) {
  const series = orderSeries(t.groupBy, t.series);
  const keys = [...series, ...(t.days.some(d => d.by['(other)']) ? ['(other)'] : [])];
  const per = t.bucket === 'month' ? 'Monthly' : 'Daily';
  return (
    <ChartCard
      title={title ?? `${per} cost${t.groupBy ? ` by ${t.groupBy}` : ''}`}
      subtitle={`${dateRange(t.period.start, t.period.end)} · ${per.toLowerCase()} average ${money(t.dailyAverage, t.currency)}${t.maxDay ? ` · peak ${shortDate(t.maxDay.date)} ${money(t.maxDay.total, t.currency)}` : ''}${t.fx ? ` · converted to ${t.fx.reportingCurrency}` : ''}`}
      table={{
        columns: [t.bucket === 'month' ? 'Month' : 'Date', ...keys.map(k => label(t.groupBy, k)), 'Total'],
        rows: t.days.map(d => [d.date, ...keys.map(k => money(d.by[k] ?? 0, t.currency)), money(d.total, t.currency)]),
      }}>
      <StackedColumns days={t.days} series={series} dim={t.groupBy} currency={t.currency} estimated={t.estimatedDays} />
    </ChartCard>
  );
}

const TOKENS = ['input', 'output', 'cache_read', 'cache_write', 'provisioned'];

export function LlmView({ llm }: { llm: Llm }) {
  const present = TOKENS.filter(tk => llm.models.some(m => m.byTokenType[tk]));
  const rows: BarRow[] = llm.models.map(m => ({
    key: m.model, label: m.model, value: m.cost, note: pct(m.shareOfLlm, 0),
    segments: present.map(tk => ({ key: tk, label: label('token_type', tk), value: m.byTokenType[tk] ?? 0, color: seriesColor('token_type', tk) })),
  }));
  const cacheRead = llm.models.reduce((s, m) => s + (m.byTokenType.cache_read ?? 0), 0);
  const cacheWrite = llm.models.reduce((s, m) => s + (m.byTokenType.cache_write ?? 0), 0);
  return (
    <ChartCard
      title="LLM cost by model and token type"
      subtitle={<>{dateRange(llm.period.start, llm.period.end)} · {money(llm.llmTotal, llm.currency)} ({pct(llm.llmShareOfAccount, 0)} of account)
        {cacheWrite > 0 && <> · cache write {money(cacheWrite, llm.currency)} vs read {money(cacheRead, llm.currency)}</>}</>}
      table={{
        columns: ['Model', ...present.map(tk => label('token_type', tk)), 'Total'],
        rows: llm.models.map(m => [m.model, ...present.map(tk => money(m.byTokenType[tk] ?? 0, llm.currency)), money(m.cost, llm.currency)]),
      }}>
      <BarList rows={rows} currency={llm.currency}
               legend={present.map(tk => ({ key: tk, label: label('token_type', tk), color: seriesColor('token_type', tk) }))} />
    </ChartCard>
  );
}

export function ProjectsView({ p }: { p: Projects }) {
  return (
    <ChartCard
      title="Cost by project"
      subtitle={`${dateRange(p.period.start, p.period.end)} · ${pct(p.allocationCoverage)} allocated (${p.groupingStatus?.mode ?? 'usage allocation'})`}
      table={{
        columns: ['Project', 'Cost', 'Share', 'Basis'],
        rows: p.projects.map(x => [x.project, money(x.cost, p.currency), pct(x.share), x.basis.join(', ')]),
      }}>
      <BarList currency={p.currency} rows={p.projects.map(x => ({
        key: x.project, label: x.project, value: x.cost, note: pct(x.share),
        color: x.project === '(unallocated)' ? 'var(--series-other)' : undefined,
      }))} />
    </ChartCard>
  );
}

function CompareView({ c }: { c: Compare }) {
  const cur = c.baseline.currency;
  return (
    <ChartCard
      title={`What changed, by ${c.groupBy.join(' & ')}`}
      subtitle={`${dateRange(c.baseline.period.start, c.baseline.period.end)} → ${dateRange(c.current.period.start, c.current.period.end)} · daily average ${money(c.totals.baselineDailyAvg, cur)} → ${money(c.totals.currentDailyAvg, cur)}`}
      table={{
        columns: [...c.groupBy, 'Baseline', 'Current', 'Change'],
        rows: c.rows.map(r => [dimLabel(c.groupBy, r), money(r.baseline, cur), money(r.current, cur), money(r.delta, cur)]),
      }}>
      <div className="tiles compact">
        <StatTile label="Baseline" value={money(c.totals.baseline, cur)} />
        <StatTile label="Current" value={money(c.totals.current, cur)} />
        <StatTile label="Change" value={`${c.totals.delta >= 0 ? '+' : ''}${money(c.totals.delta, cur)}`} />
      </div>
      <DeltaBars currency={cur} rows={c.rows.map(r => ({
        key: dimLabel(c.groupBy, r), label: dimLabel(c.groupBy, r), delta: r.delta, baseline: r.baseline, current: r.current, deltaPct: r.deltaPct,
      }))} />
    </ChartCard>
  );
}

export function AnomaliesView({ a, onAsk }: { a: Anomalies; onAsk?: (prompt: string) => void }) {
  return (
    <ChartCard title="Anomalies"
               subtitle={`${dateRange(a.period.start, a.period.end)} · days above the trailing ${a.groupBy} mean (support and tax excluded)`}>
      {a.anomalies.length === 0 ? <p className="empty">No unusual days in this period.</p> : (
        <ul className="anomalies">
          {a.anomalies.slice(0, 8).map(x => {
            const key = label(a.groupBy, String(x[a.groupBy] ?? '-'));
            return (
              <li key={`${x.date}-${key}`}>
                <span className="status-icon" aria-hidden>▲</span>
                <div className="anomaly-main">
                  <strong>{money(x.cost, a.currency)}</strong>
                  <span>{key} on {shortDate(x.date)}</span>
                  <span className="muted">
                    usual {money(x.trailingMean, a.currency)}/day{x.ratio ? ` · ${x.ratio.toFixed(1)}×` : ''}
                  </span>
                </div>
                {onAsk && (
                  <button className="link" onClick={() => onAsk(`What caused the ${key} cost spike on ${x.date}? Break it down by model, token type and usage type.`)}>
                    Ask why
                  </button>
                )}
              </li>
            );
          })}
        </ul>
      )}
    </ChartCard>
  );
}

function ProjectionView({ p }: { p: Projection }) {
  if (p.error) return <p className="tool-error">{p.error}</p>;
  const f = p.costExplorerForecast;
  return (
    <ChartCard title={`Month-end projection · ${p.month}`}
               subtitle={`${p.daysElapsed} of ${p.daysInMonth} days · support and tax treated as fixed`}>
      <div className="tiles compact">
        <StatTile label="Month to date" value={money(p.monthToDate, p.currency)} />
        <StatTile label="At month-to-date rate" value={money(p.projectedAtMtdRate, p.currency)} />
        <StatTile label="At last-7-day rate" value={money(p.projectedAtLast7DayRate, p.currency)} />
        {f && !f.error && f.remainingMonthForecast && (
          <StatTile label="AWS forecast" value={money(p.monthToDate + Number(f.remainingMonthForecast), p.currency)}
                    detail="month to date + Cost Explorer forecast" />
        )}
      </div>
      {f?.error && <p className="muted">AWS forecast: {f.error}</p>}
    </ChartCard>
  );
}

interface ResourcesResult {
  projects: Record<string, Record<string, number>>;
  resourcesWithoutProjectTag: number;
}
function ResourcesView({ r }: { r: ResourcesResult }) {
  const rows = Object.entries(r.projects ?? {}).flatMap(([p, types]) => Object.entries(types).map(([t, n]) => [p, t, n]));
  return (
    <ChartCard title="Tagged resources by project" subtitle={`${r.resourcesWithoutProjectTag} resources without a project tag`}>
      <DataTable columns={['Project', 'Resource type', 'Count']} rows={rows} />
    </ChartCard>
  );
}
