import { useEffect, useRef, useState, type ReactNode } from 'react';
import { fetchReport, newSessionId } from '../lib/agent';
import { label } from '../lib/colors';
import { dateRange, money, pct } from '../lib/format';
import type { Report } from '../lib/types';
import { ErrorBoundary } from './ErrorBoundary';
import { StatTile } from './StatTile';
import { AnomaliesView, LlmView, ProjectsView, SummaryView, TrendView } from './ToolResultView';

const monthName = (ym?: string) =>
  ym ? new Date(`${ym}-15T00:00:00Z`).toLocaleString('en-US', { month: 'long', timeZone: 'UTC' }) : 'month';

type Preset = { key: string; label: string; days: (r?: Report) => number };
const PRESETS: Preset[] = [
  { key: '7', label: 'Last 7 days', days: () => 7 },
  { key: '30', label: 'Last 30 days', days: () => 30 },
  { key: '90', label: 'Last 90 days', days: () => 90 },
  { key: 'mtd', label: 'Month to date', days: r => (r ? Number(r.status.coverage.lastDay.slice(8, 10)) : 30) },
];

export function Dashboard({ onAsk }: { onAsk: (prompt: string) => void }) {
  const session = useRef(newSessionId());
  const [preset, setPreset] = useState('30');
  const [report, setReport] = useState<Report | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    const days = PRESETS.find(p => p.key === preset)!.days(report ?? undefined);
    setLoading(true);
    fetchReport(days, session.current)
      .then(r => live && (setReport(r), setError(null)))
      .catch(e => live && setError(e instanceof Error ? e.message : String(e)))
      .finally(() => live && setLoading(false));
    return () => { live = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [preset]);

  const r = report;
  const cur = r?.byGroup.currency ?? 'USD';
  const groupCost = (g: string) => r?.byGroup.rows.find(x => x.group === g)?.cost ?? 0;

  return (
    <main className="dashboard">
      <div className="filters" role="toolbar" aria-label="Period">
        {PRESETS.map(p => (
          <button key={p.key} className={`chip ${preset === p.key ? 'on' : ''}`} onClick={() => setPreset(p.key)}>
            {preset === p.key && <span className="check" aria-hidden>✓</span>}{p.label}
          </button>
        ))}
        {r && (
          <span className="freshness">
            Data through {r.status.coverage?.lastDay ?? '–'}
            {r.status.updatedAt && !Number.isNaN(Date.parse(r.status.updatedAt)) && <> · collected {new Date(r.status.updatedAt).toLocaleString()}</>}
            {r.byGroup.estimatedDays.length > 0 && <> · {r.byGroup.estimatedDays.length} days still estimated</>}
          </span>
        )}
      </div>

      {error && <div className="banner error">Could not load the report: {error}</div>}
      {r?.byGroup.staleProviders && (
        <div className="banner">
          Not current, left out of the default period:{' '}
          {Object.entries(r.byGroup.staleProviders).map(([p, d]) => `${label('provider', p)} (data through ${d})`).join(', ')}
        </div>
      )}
      {!r && loading && <div className="banner">Loading cost report…</div>}

      {r && (
        <div className={`dash-body ${loading ? 'refreshing' : ''}`}>
          <div className="tiles">
            <StatTile hero label={`Total spend · ${dateRange(r.byGroup.period.start, r.byGroup.period.end)}`}
                      value={money(r.byGroup.total, cur)}
                      detail={`daily average ${money(r.dailyByGroup.dailyAverage, cur)}`} />
            <StatTile label={`Projected ${monthName(r.projection.month)} total`}
                      value={r.projection.error ? '–' : `${money(r.projection.projectedAtMtdRate, cur)} – ${money(r.projection.projectedAtLast7DayRate, cur)}`}
                      detail={r.projection.error ?? `month to date ${money(r.projection.monthToDate, cur)} · ${r.projection.daysElapsed}/${r.projection.daysInMonth} days`} />
            <StatTile label={label('group', 'llm')} value={money(groupCost('llm'), cur)}
                      detail={`${pct(r.llm.llmShareOfAccount, 0)} of spend · ${r.llm.models[0]?.model ?? '–'} leads`} />
            <StatTile label="Infrastructure + networking" value={money(groupCost('infrastructure') + groupCost('networking'), cur)}
                      detail={`networking ${money(groupCost('networking'), cur)}`} />
          </div>

          <div className="grid">
            {/* one boundary per card: a bad section shows an error in place, the rest of the dashboard stays */}
            {r.dailyByProvider && r.byProvider && (
              <>
                <Card name="daily cost by provider" r={r}><TrendView t={r.dailyByProvider} title="Daily cost by cloud provider" /></Card>
                <Card name="spend by provider" r={r}><SummaryView s={r.byProvider} title="Spend by cloud provider" /></Card>
              </>
            )}
            <Card name="daily cost by group" r={r}><TrendView t={r.dailyByGroup} title="Daily cost by group" /></Card>
            <Card name="spend by category" r={r}><SummaryView s={r.byCategory} title="Spend by category" /></Card>
            <Card name="LLM cost" r={r}><LlmView llm={r.llm} /></Card>
            <Card name="top services" r={r}><SummaryView s={r.topServices} title="Top services" /></Card>
            <Card name="anomalies" r={r}><AnomaliesView a={r.anomalies} onAsk={onAsk} /></Card>
            <Card name="projects" r={r}><ProjectsView p={r.projects} /></Card>
          </div>
        </div>
      )}
    </main>
  );
}

function Card({ name, r, children }: { name: string; r: Report; children: ReactNode }) {
  return <ErrorBoundary label={name} resetKey={r}>{children}</ErrorBoundary>;
}
