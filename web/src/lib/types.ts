/** Shapes returned by finops.analysis (app/finops_agent/finops/analysis.py), via the report action or tool results. */

export interface Meta {
  period: { start: string; end: string; days: number };
  currency: string;
  dataUpdatedAt?: string;
  estimatedDays: string[];
  missingDays: string[];
  /** present when more than one cloud provider is configured */
  providers?: string[];
  missingDaysByProvider?: Record<string, number>;
  /** providers whose data ends well before the others' (excluded from the default period end) */
  staleProviders?: Record<string, string>;
  fx?: { reportingCurrency: string; rates: Record<string, number>; note: string };
}

export type Dims = Record<string, string | number | null | undefined>;

export interface SummaryRow extends Dims {
  cost: number;
  share: number;
}
export interface Summary extends Meta {
  groupBy: string[];
  filters?: Record<string, string>;
  total: number;
  rows: SummaryRow[];
  notShown: { count: number; cost: number };
  /** date/month x dimension: series beyond top_n summed as "(other)" */
  seriesFolded?: { count: number; cost: number; into: string };
}

export interface TrendDay {
  date: string;
  total: number;
  by: Record<string, number>;
}
export interface Trend extends Meta {
  groupBy: string;
  /** "date" (default) or "month": what TrendDay.date holds */
  bucket?: 'date' | 'month';
  series: string[];
  days: TrendDay[];
  dailyAverage: number;
  maxDay: TrendDay | null;
}

export interface LlmModel {
  model: string;
  cost: number;
  shareOfLlm: number;
  byTokenType: Record<string, number>;
  byRegionalScope: Record<string, number>;
}
export interface Llm extends Meta {
  llmTotal: number;
  accountTotal: number;
  llmShareOfAccount: number;
  llmDailyAverage: number;
  models: LlmModel[];
  note?: string;
}

export interface Project {
  project: string;
  cost: number;
  share: number;
  basis: string[];
  byCategory: Record<string, number>;
}
export interface Projects extends Meta {
  total: number;
  allocated: number;
  allocationCoverage: number;
  projects: Project[];
  groupingStatus?: { mode: string };
}

export interface Anomaly extends Dims {
  date: string;
  cost: number;
  trailingMean: number;
  excess: number;
  ratio: number | null;
}
export interface Anomalies extends Meta {
  groupBy: string;
  anomalies: Anomaly[];
  count: number;
}

export interface Projection extends Meta {
  month: string;
  daysElapsed: number;
  daysInMonth: number;
  monthToDate: number;
  monthToDateByGroup: Record<string, number>;
  fixedCharged: number;
  projectedAtMtdRate: number;
  projectedAtLast7DayRate: number;
  costExplorerForecast?: { remainingMonthForecast?: string; lower80?: string; upper80?: string; error?: string };
  error?: string;
}

export interface CompareRow extends Dims {
  baseline: number;
  current: number;
  delta: number;
  deltaPct: number | null;
  dailyAvgDelta: number;
}
export interface Compare {
  baseline: Meta;
  current: Meta;
  groupBy: string[];
  totals: { baseline: number; current: number; delta: number; baselineDailyAvg: number; currentDailyAvg: number };
  rows: CompareRow[];
}

/** plot_cost: a chart spec plus the deterministic data it is drawn from. */
export interface ChartSpec {
  kind: 'stacked_columns' | 'bars';
  x: string;
  series: string;
  title: string;
}
export interface BreakdownRow {
  key: string;
  cost: number;
  share: number;
  by?: Record<string, number>;
}
export interface Breakdown extends Meta {
  x: string;
  seriesDim: string;
  series: string[];
  total: number;
  rows: BreakdownRow[];
  notShown: { count: number; cost: number };
  filters?: Record<string, string>;
}
export type Plot = { chart: ChartSpec } & (Trend | Breakdown);

export interface Report {
  status: {
    updatedAt: string; coverage: { firstDay: string; lastDay: string; lastDayByProvider?: Record<string, string> };
    projectGrouping: { mode: string }; providers?: Record<string, unknown>;
  };
  byGroup: Summary;
  dailyByGroup: Trend;
  byCategory: Summary;
  topServices: Summary;
  llm: Llm;
  projects: Projects;
  anomalies: Anomalies;
  projection: Projection;
  byProvider?: Summary;
  dailyByProvider?: Trend;
}

/** Events of {"prompt": ..., "events": true}: text deltas are plain strings. */
export type AgentEvent =
  | string
  | { type: 'tool_call'; id: string; name: string; input: Record<string, unknown> }
  | { type: 'tool_result'; id: string; name: string; status: 'success' | 'error' | string; input: Record<string, unknown>; result: unknown }
  | { error: string };

/** {"action": "status"}: configured clouds and data freshness for the "How it works" page (no model call). */
export interface ProviderStatus {
  provider: string;
  source: 'costExplorer' | 'focus' | null;
  costMetric: string | null;
  currency: string | null;
  updatedAt: string | null;
  coverage: { firstDay?: string; lastDay?: string } | null;
  estimatedDays: number;
  projectGrouping: string | null;
  stale: boolean;
  hasData: boolean;
}
export interface SystemStatus {
  providers: ProviderStatus[];
  coverage: { firstDay?: string; lastDay?: string } | null;
  staleAfterDays: number;
  reportingCurrency: string | null;
  projectTagKeys: string[];
  model: string;
  tools: string[];
}
