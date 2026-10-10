import { BarList, type BarRow } from '../charts/BarList';
import { DeltaBars } from '../charts/DeltaBars';
import { StackedColumns } from '../charts/StackedColumns';
import { CATEGORY_GROUP, GROUPS, label, seriesColor } from '../lib/colors';
import { dateRange, money, pct, shortDate } from '../lib/format';
import type { Anomalies, Compare, Dims, Llm, Projection, Projects, Summary, Trend, TrendDay } from '../lib/types';
import { ChartCard, DataTable } from './ChartCard';
import { StatTile } from './StatTile';

/** Charts for a tool result; every number comes from finops.analysis, not from the model's text. */
export function ToolResultView({ name, result }: { name: string; result: unknown }) {
  if (!result || typeof result !== 'object') return null;
  if ('error' in result) return <p className="tool-error">{name}: {String((result as { error: unknown }).error)}</p>;
  switch (name) {
    case 'cost_summary': return <SummaryView s={result as Summary} />;
    case 'daily_trend': return <TrendView t={result as Trend} />;
    case 'llm_costs': return <LlmView llm={result as Llm} />;
    case 'project_costs': return <ProjectsView p={result as Projects} />;
    case 'compare_periods': return <CompareView c={result as Compare} />;
    case 'find_anomalies': return <AnomaliesView a={result as Anomalies} />;
    case 'month_end_projection': return <ProjectionView p={result as Projection} />;
    case 'project_resources': return <ResourcesView r={result as ResourcesResult} />;
    default: return null; // data_status: shown as a step only
  }
}

const dimLabel = (dims: string[], row: Dims) =>
  dims.map(d => label(d, String(row[d] ?? '-'))).join(' · ');

function filtersText(f: Record<string, string> | undefined) {
  const e = Object.entries(f ?? {});
  return e.length ? ` · ${e.map(([k, v]) => `${k}: ${v}`).join(', ')}` : '';
}

export function SummaryView({ s, title }: { s: Summary; title?: string }) {
  const dims = s.groupBy;
  const sub = `${dateRange(s.period.start, s.period.end)} · total ${money(s.total, s.currency)}${filtersText(s.filters)}`;
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
  const identity = dim === 'group' || dim === 'category' || dim === 'token_type';
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
  return (
    <ChartCard
      title={title ?? `Daily cost by ${t.groupBy}`}
      subtitle={`${dateRange(t.period.start, t.period.end)} · daily average ${money(t.dailyAverage, t.currency)}${t.maxDay ? ` · peak ${shortDate(t.maxDay.date)} ${money(t.maxDay.total, t.currency)}` : ''}`}
      table={{
        columns: ['Date', ...keys.map(k => label(t.groupBy, k)), 'Total'],
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
