import { useEffect, useRef, useState, type ReactNode } from 'react';
import { fetchStatus, newSessionId } from '../lib/agent';
import { GROUPS, label, seriesColor } from '../lib/colors';
import type { ProviderStatus, SystemStatus } from '../lib/types';
import { DataTable } from './ChartCard';

const PROVIDER_NAMES: Record<string, string> = { aws: 'AWS', azure: 'Azure', gcp: 'Google Cloud', oci: 'Oracle Cloud' };
const SOURCE: Record<string, { name: string; detail: string }> = {
  aws: { name: 'Cost Explorer API', detail: 'daily, service × usage type, + AgentCore usage from CloudWatch' },
  azure: { name: 'Cost Management export', detail: 'dataset "Cost and usage details (FOCUS)" → Blob storage' },
  gcp: { name: 'Cloud Billing FOCUS export', detail: 'BigQuery table → scheduled EXPORT DATA → Cloud Storage' },
  oci: { name: 'FOCUS reports', detail: 'usage-report bucket' },
};

const SECTIONS = [
  { id: 'architecture', label: 'Architecture' },
  { id: 'requests', label: 'What happens when you…' },
  { id: 'focus', label: 'FOCUS' },
  { id: 'data', label: 'Data behind the scenes' },
  { id: 'controls', label: 'How the agent is controlled' },
];

/** The "How it works" tab: architecture, FOCUS, and how cost data is collected, stored and analysed. */
export function HowItWorks() {
  const session = useRef(newSessionId());
  const [status, setStatus] = useState<SystemStatus | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    fetchStatus(session.current)
      .then(s => { if (live) setStatus(s); })
      .catch(e => { if (live) setError(e instanceof Error ? e.message : String(e)); });
    return () => { live = false; };
  }, []);

  const configured = new Set(status?.providers.map(p => p.provider) ?? ['aws']);

  return (
    <main className="dashboard hiw">
      <header className="hiw-hero">
        <h1>How this FinOps agent works</h1>
        <p>
          Every number on this page and in the answers is computed by code from your billing data. The model chooses
          which analysis to run and explains the result - it never does the arithmetic, and never merges clouds itself.
        </p>
        <nav className="filters" aria-label="Sections">
          {SECTIONS.map(s => <a key={s.id} className="chip" href={`#how-it-works/${s.id}`}
                                onClick={e => { e.preventDefault(); scrollTo(s.id); }}>{s.label}</a>)}
        </nav>
      </header>

      <LiveStatus status={status} error={error} />

      <Section id="architecture" title="Architecture"
               lead="Billing data is collected once a day, normalized to one shape per cloud, stored in S3, and read by the agent. The browser never sees your cloud credentials.">
        <Architecture configured={configured} status={status} />
      </Section>

      <Section id="requests" title="What happens when you…">
        <div className="hiw-grid two">
          <Steps title="open the dashboard" tag="no model call" steps={[
            <>The page sends <code>{'{"action": "report", "days": 30}'}</code> to the AgentCore Runtime.</>,
            <>The runtime reads the stored month documents of every configured cloud from S3.</>,
            <><code>analysis.report()</code> computes totals, daily trends, categories, LLM cost, projects, anomalies and the month-end projection.</>,
            <>The JSON comes back and is drawn as-is. Same input, same numbers - every time.</>,
          ]} />
          <Steps title="ask a question" tag="model + tools" steps={[
            <>Your question goes to the agent (Strands + {status?.model ?? 'Claude on Bedrock'}).</>,
            <>The model picks a tool and its arguments - e.g. <code>plot_cost(x="date", series="provider")</code>. Unknown arguments are rejected.</>,
            <>The tool runs deterministic code over the same stored data and returns JSON.</>,
            <>Each tool result streams to this page and is charted directly; the model's text explains it.</>,
          ]} />
        </div>
      </Section>

      <Section id="focus" title="FOCUS - one billing standard for every cloud"
               lead="FOCUS (FinOps Open Cost and Usage Specification) is an open standard from the FinOps Foundation (Linux Foundation) for cloud billing data. A FOCUS export is a provider's billing detail delivered in that format: the provider does the mapping, so every cloud's file has the same columns with the same meaning.">
        <Focus />
      </Section>

      <Section id="data" title="Data behind the scenes">
        <DataBehind status={status} />
      </Section>

      <Section id="controls" title="How the agent is controlled"
               lead="Four layers, from soft to hard. The harder the layer, the more it is trusted for correctness.">
        <Controls tools={status?.tools} />
      </Section>
    </main>
  );
}

function scrollTo(id: string) {
  document.getElementById(id)?.scrollIntoView({ behavior: 'smooth', block: 'start' });
  try { history.replaceState(null, '', `#how-it-works/${id}`); } catch { /* sandboxed frames */ }
}

function Section({ id, title, lead, children }: { id: string; title: string; lead?: string; children: ReactNode }) {
  return (
    <section id={id} className="hiw-section">
      <h2>{title}</h2>
      {lead && <p className="hiw-lead">{lead}</p>}
      {children}
    </section>
  );
}

// ---------------------------------------------------------------------------------------------------------
// live status
// ---------------------------------------------------------------------------------------------------------
function LiveStatus({ status, error }: { status: SystemStatus | null; error: string | null }) {
  if (error) return <div className="banner error">Could not load the live status: {error}</div>;
  if (!status) return <div className="banner">Loading the live status…</div>;
  return (
    <section className="card hiw-status" aria-label="Live status">
      <header className="card-head">
        <div>
          <h3>Your deployment, right now</h3>
          <p className="card-sub">
            Cross-cloud data through {status.coverage?.lastDay ?? '–'} · {status.tools.length} tools · model <code>{status.model}</code>
            {status.reportingCurrency && <> · reporting currency {status.reportingCurrency}</>}
          </p>
        </div>
      </header>
      <div className="hiw-providers">
        {status.providers.map(p => <ProviderTile key={p.provider} p={p} staleAfter={status.staleAfterDays} />)}
      </div>
    </section>
  );
}

function ProviderTile({ p, staleAfter }: { p: ProviderStatus; staleAfter: number }) {
  const state = !p.hasData ? 'No data yet' : p.stale ? `Stale (> ${staleAfter} days behind)` : 'Current';
  return (
    <div className={`hiw-provider ${!p.hasData || p.stale ? 'warn' : ''}`}>
      <div className="hiw-provider-head">
        <span className="swatch" style={{ background: seriesColor('provider', p.provider) }} />
        <strong>{PROVIDER_NAMES[p.provider] ?? p.provider}</strong>
        <span className={`hiw-badge ${!p.hasData || p.stale ? 'warn' : 'ok'}`}>{state}</span>
      </div>
      <dl>
        <dt>Source</dt><dd>{p.source === 'costExplorer' ? 'Cost Explorer API' : p.source === 'focus' ? 'FOCUS export' : '–'}</dd>
        <dt>Data through</dt><dd>{p.coverage?.lastDay ?? '–'}</dd>
        <dt>Cost metric</dt><dd>{p.costMetric ?? '–'}{p.currency ? ` · ${p.currency}` : ''}</dd>
        <dt>Projects by</dt><dd>{groupingLabel(p.projectGrouping)}</dd>
        {p.estimatedDays > 0 && <><dt>Still estimated</dt><dd>{p.estimatedDays} recent days</dd></>}
      </dl>
    </div>
  );
}

const groupingLabel = (mode: string | null) =>
  ({ costCategory: 'cost category', tag: 'tag', usageAllocation: 'AgentCore usage', none: 'no tags found' } as Record<string, string>)[mode ?? ''] ?? '–';

// ---------------------------------------------------------------------------------------------------------
// architecture
// ---------------------------------------------------------------------------------------------------------
function Architecture({ configured, status }: { configured: Set<string>; status: SystemStatus | null }) {
  const clouds = ['aws', 'azure', 'gcp', ...(configured.has('oci') ? ['oci'] : [])];
  return (
    <div className="arch" role="list" aria-label="Data flow, left to right">
      <Stage n={1} title="Billing sources" note="read-only">
        {clouds.map(p => (
          <Node key={p} dim={!configured.has(p)} title={`${PROVIDER_NAMES[p]} · ${SOURCE[p].name}`}
                detail={configured.has(p) ? SOURCE[p].detail : 'not configured - set FINOPS_FOCUS_PROVIDERS'}
                swatch={seriesColor('provider', p)} />
        ))}
      </Stage>
      <Stage n={2} title="Collect & normalize" note="AWS Lambda">
        <Node title="Collector" detail="EventBridge daily 01:00 UTC; re-reads the last 7 days (Cost Explorer revises them); 2-3 requests/run" />
        <Node title="FocusIngest" detail="runs when an export lands in landing/<provider>/ (copied by DataSync); aggregates FOCUS rows" />
      </Stage>
      <Stage n={3} title="Store" note="Amazon S3">
        <Node title="finops/v1/" detail="AWS: status.json + months/YYYY-MM.json" />
        <Node title="finops/v1/<provider>/" detail="one prefix per cloud, same document shape" />
      </Stage>
      <Stage n={4} title="Agent" note="AgentCore Runtime">
        <Node title={`Strands + ${status ? shortModel(status.model) : 'Claude'}`} detail="chooses tools, explains results" />
        <Node title={`${status?.tools.length ?? 10} tools → analysis.py`} detail="deterministic numbers; MultiStore reads every cloud as one dataset" />
      </Stage>
      <Stage n={5} title="This app" note="Amplify + Cognito">
        <Node title="Dashboard" detail='{"action": "report"} - no model call' />
        <Node title="Ask panel" detail="streamed tool results → charts, model text → answer" />
      </Stage>
    </div>
  );
}

const shortModel = (id: string) => id.replace(/^(global|us|eu|apac)\./, '').replace(/^anthropic\./, '');

function Stage({ n, title, note, children }: { n: number; title: string; note: string; children: ReactNode }) {
  return (
    <div className="arch-stage" role="listitem">
      <div className="arch-stage-head"><span className="arch-n">{n}</span><strong>{title}</strong><span className="muted">{note}</span></div>
      <div className="arch-nodes">{children}</div>
    </div>
  );
}

function Node({ title, detail, swatch, dim }: { title: string; detail: string; swatch?: string; dim?: boolean }) {
  return (
    <div className={`arch-node ${dim ? 'dim' : ''}`}>
      <div className="arch-node-title">{swatch && <span className="swatch" style={{ background: swatch }} />}{title}</div>
      <div className="arch-node-detail">{detail}</div>
    </div>
  );
}

// ---------------------------------------------------------------------------------------------------------
// requests
// ---------------------------------------------------------------------------------------------------------
function Steps({ title, tag, steps }: { title: string; tag: string; steps: ReactNode[] }) {
  return (
    <div className="card">
      <header className="card-head"><h3>…{title}</h3><span className="hiw-badge">{tag}</span></header>
      <ol className="hiw-steps">{steps.map((s, i) => <li key={i}>{s}</li>)}</ol>
    </div>
  );
}

// ---------------------------------------------------------------------------------------------------------
// FOCUS
// ---------------------------------------------------------------------------------------------------------
function Focus() {
  return (
    <div className="hiw-grid">
      <div className="card">
        <header className="card-head"><div><h3>Why it is needed</h3>
          <p className="card-sub">Before FOCUS, every cloud named and defined the same things differently</p></div></header>
        <DataTable
          columns={['', 'AWS (CUR)', 'Azure (cost details)', 'Google Cloud (BigQuery)', 'FOCUS']}
          rows={[
            ['Amount', 'line_item_unblended_cost', 'costInBillingCurrency', 'cost', 'BilledCost'],
            ['Amortized amount', 'separate columns', 'separate dataset', 'derived from credits', 'EffectiveCost'],
            ['Service', 'product_servicecode', 'meterCategory', 'service.description', 'ServiceName, ServiceCategory'],
            ['Usage detail', 'line_item_usage_type', 'meterName', 'sku.description', 'SkuMeter'],
            ['Sub-account', 'line_item_usage_account_id', 'SubscriptionId', 'project.id', 'SubAccountId'],
          ]} />
      </div>

      <div className="hiw-grid two">
        <div className="card">
          <header className="card-head"><h3>Columns this agent relies on</h3></header>
          <dl className="hiw-defs">
            <dt><code>BilledCost</code></dt><dd>What is invoiced - comparable to AWS unblended cost. The default cost metric.</dd>
            <dt><code>EffectiveCost</code></dt><dd>Cost after amortizing commitments (RIs, Savings Plans, CUDs) - best for showback.</dd>
            <dt><code>ServiceCategory</code></dt><dd>Standard classes (Compute, Storage, Networking, AI and Machine Learning…) mapped to this app's groups.</dd>
            <dt><code>ChargeCategory</code></dt><dd>Usage, Purchase, Tax, Credit, Adjustment.</dd>
            <dt><code>BillingPeriod* / ChargePeriod*</code></dt><dd>Which invoice a charge belongs to, and when it happened - used to book corrections in the right period.</dd>
            <dt><code>Tags</code></dt><dd>Normalized resource tags - project showback across clouds.</dd>
            <dt><code>x_*</code></dt><dd>Provider extensions, e.g. AWS <code>x_UsageType</code>.</dd>
          </dl>
        </div>
        <div className="card">
          <header className="card-head"><div><h3>Where each cloud's FOCUS export comes from</h3>
            <p className="card-sub">Status as researched in October 2026</p></div></header>
          <DataTable
            columns={['Cloud', 'Configure', 'Delivered to', 'Status']}
            rows={[
              ['AWS', 'Billing → Data Exports, "FOCUS 1.2 with AWS columns"', 'S3 (Parquet / CSV)', 'GA'],
              ['Azure', 'Cost Management → Exports, "Cost and usage details (FOCUS)"', 'Storage account + manifest.json', '1.2-preview'],
              ['Google Cloud', 'Cloud Billing BigQuery export, FOCUS option', 'BigQuery table', 'Preview'],
            ]} />
          <ul className="hiw-notes">
            <li>Exports refresh daily with month-to-date detail; recent days are revised (≈ 1 day latency).</li>
            <li>The spec is at 1.4, but providers emit about <strong>1.2</strong> - the practical common baseline.</li>
            <li><strong>No standard LLM token columns yet</strong> (planned for 1.5): model and token type are parsed from <code>SkuMeter</code>, e.g. <code>gpt-4.1-Inp-glbl Tokens</code> → gpt-4.1 · input.</li>
            <li>FOCUS says <em>what was spent</em>, not <em>what to change</em>: savings advice needs each cloud's recommendation APIs.</li>
          </ul>
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------------------------------------
// data behind the scenes
// ---------------------------------------------------------------------------------------------------------
function DataBehind({ status }: { status: SystemStatus | null }) {
  const keys = status?.projectTagKeys.join(', ') || 'project, Project, agentcore:project-name, app';
  return (
    <div className="hiw-grid three">
      <Fact title="Collection & revisions">
        <p>AWS: a Lambda reads Cost Explorer daily (service × usage type) and <strong>replaces the last 7 days</strong>, because AWS keeps revising them. Days still marked estimated are flagged in every answer.</p>
        <p>Other clouds: when an export run lands, <code>FocusIngest</code> replaces the days of that run's billing period. Azure runs are ingested only when every file in the manifest has arrived.</p>
      </Fact>
      <Fact title="Storage layout">
        <pre className="hiw-tree">{`s3://<bucket>/finops/v1/
├── status.json        coverage
├── months/2026-10.json  AWS
├── azure/
│   ├── status.json
│   └── months/…
└── gcp/ …
landing/<provider>/<run>/
  raw exports (input only)`}</pre>
        <p>FOCUS rows (one per resource per hour/day) are aggregated to <strong>day × service × meter × category × region × sub-account</strong>, the same grain as Cost Explorer.</p>
      </Fact>
      <Fact title="Classification at read time">
        <p>Each line is classified when it is read, so a rule change re-classifies all history without re-collecting it.</p>
        <ul className="hiw-groups">
          {GROUPS.map(g => <li key={g}><span className="swatch" style={{ background: seriesColor('group', g) }} />{label('group', g)}</li>)}
        </ul>
        <p>LLM lines (Bedrock, Azure OpenAI / Foundry, Vertex AI) keep model and token type; data transfer, NAT and load balancers are networking whichever service bills them.</p>
      </Fact>
      <Fact title="Project allocation">
        <p>Best source available, per day:</p>
        <ol className="hiw-steps compact">
          <li>AWS Cost Category (payer account)</li>
          <li>Cost allocation tag / FOCUS <code>Tags</code> - keys tried: <code>{keys}</code></li>
          <li>AgentCore usage: runtime vCPU / memory split by measured usage; the rest is "(unallocated)"</li>
        </ol>
      </Fact>
      <Fact title="Freshness across clouds">
        <p>The default period ends at the <strong>earliest</strong> last day among current clouds, so a cross-cloud total never mixes a complete day of one cloud with a missing day of another.</p>
        <p>A cloud more than {status?.staleAfterDays ?? 3} days behind is shown as stale instead of holding everyone back.</p>
      </Fact>
      <Fact title="Currencies, corrections, access">
        <p>Clouds billing in different currencies are <strong>refused, not summed</strong>, unless a reporting currency and fixed FX rates are configured.</p>
        <p>Corrections to an earlier period are booked in the period that invoices them, so re-ingesting a run gives the same result.</p>
        <p>The agent can only <strong>read</strong> its S3 prefix; only an explicitly requested AWS forecast calls Cost Explorer (USD 0.01, capped per session).</p>
      </Fact>
    </div>
  );
}

function Fact({ title, children }: { title: string; children: ReactNode }) {
  return <div className="card hiw-fact"><header className="card-head"><h3>{title}</h3></header>{children}</div>;
}

// ---------------------------------------------------------------------------------------------------------
// controls
// ---------------------------------------------------------------------------------------------------------
function Controls({ tools }: { tools?: string[] }) {
  const layers = [
    { name: 'System prompt', where: 'main.py', what: 'Behaviour: answer only from tools, check data freshness first, state period and currency, lead with the answer, use plot_cost for charts, at most 3 grounded suggestions.' },
    { name: 'Tool design', where: 'finops/tools.py', what: `What can be analysed: ${tools?.length ?? 10} analysis actions (not free-form SQL). Their descriptions are what the model reads.` },
    { name: 'Deterministic analysis', where: 'finops/analysis.py · classify.py', what: 'How cost is analysed: taxonomy, anomaly rule (trailing 7-day mean, > 2σ and > +50 %), projections, allocation, currency.' },
    { name: 'Guardrails', where: 'StrictToolArgs · result contract · UI checks', what: 'Unknown arguments rejected; every failure is a JSON error the model can fix; this page only charts results that pass a shape check, each in its own error boundary.' },
  ];
  return (
    <div className="hiw-grid">
      <ol className="hiw-layers">
        {layers.map((l, i) => (
          <li key={l.name} style={{ ['--depth' as string]: i }}>
            <div><strong>{l.name}</strong> <span className="muted">{l.where}</span></div>
            <p>{l.what}</p>
          </li>
        ))}
      </ol>
      {tools && tools.length > 0 && (
        <div className="hiw-tools"><span className="muted">Tools</span>{tools.map(t => <code key={t}>{t}</code>)}</div>
      )}
    </div>
  );
}
