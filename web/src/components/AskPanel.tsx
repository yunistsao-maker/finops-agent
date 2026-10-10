import { useEffect, useRef, useState, type FormEvent } from 'react';
import Markdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { invokeAgent, newSessionId } from '../lib/agent';
import type { AgentEvent } from '../lib/types';
import { ToolResultView } from './ToolResultView';

type Part =
  | { kind: 'text'; text: string }
  | { kind: 'tool'; id: string; name: string; input: Record<string, unknown>; done: boolean; result?: unknown };
type Msg = { role: 'user'; text: string } | { role: 'assistant'; parts: Part[]; done: boolean; error?: string };

const SUGGESTIONS = [
  'What did we spend on LLMs vs infrastructure vs networking this month?',
  'Show daily LLM cost by model for the last 14 days',
  'Compare the last 7 days with the 7 days before',
  'How good is our prompt-cache usage, and what would save the most?',
  "Projected month-end total, including AWS's forecast",
];

const TOOL_LABELS: Record<string, string> = {
  data_status: 'Checked data freshness', cost_summary: 'Summarized cost', daily_trend: 'Built daily trend',
  llm_costs: 'Broke down LLM cost', project_costs: 'Allocated cost to projects', compare_periods: 'Compared periods',
  find_anomalies: 'Scanned for anomalies', month_end_projection: 'Projected month end', project_resources: 'Listed project resources',
};

function apply(parts: Part[], e: AgentEvent): Part[] {
  if (typeof e === 'string') {
    const last = parts[parts.length - 1];
    return last?.kind === 'text' ? [...parts.slice(0, -1), { kind: 'text', text: last.text + e }] : [...parts, { kind: 'text', text: e }];
  }
  if ('type' in e && e.type === 'tool_call') return [...parts, { kind: 'tool', id: e.id, name: e.name, input: e.input, done: false }];
  if ('type' in e && e.type === 'tool_result') {
    return parts.map(p => (p.kind === 'tool' && p.id === e.id ? { ...p, done: true, result: e.result } : p));
  }
  return parts;
}

export function AskPanel({ pending, onPendingTaken }: { pending: string | null; onPendingTaken: () => void }) {
  const [session, setSession] = useState(newSessionId);
  const [msgs, setMsgs] = useState<Msg[]>([]);
  const [input, setInput] = useState('');
  const [busy, setBusy] = useState(false);
  const abort = useRef<AbortController | null>(null);
  const end = useRef<HTMLDivElement>(null);

  useEffect(() => end.current?.scrollIntoView({ block: 'end' }), [msgs]);

  async function ask(prompt: string) {
    if (!prompt.trim() || busy) return;
    setInput('');
    setBusy(true);
    setMsgs(m => [...m, { role: 'user', text: prompt }, { role: 'assistant', parts: [], done: false }]);
    const update = (fn: (a: Extract<Msg, { role: 'assistant' }>) => Partial<Extract<Msg, { role: 'assistant' }>>) =>
      setMsgs(m => {
        const last = m[m.length - 1];
        return last?.role === 'assistant' ? [...m.slice(0, -1), { ...last, ...fn(last) }] : m;
      });
    abort.current = new AbortController();
    try {
      await invokeAgent({ prompt, events: true }, session, e => {
        if (typeof e === 'object' && 'error' in e) update(() => ({ error: e.error }));
        else update(a => ({ parts: apply(a.parts, e) }));
      }, abort.current.signal);
      update(() => ({ done: true }));
    } catch (err) {
      const aborted = abort.current?.signal.aborted;
      update(() => ({ done: true, error: aborted ? 'Stopped.' : err instanceof Error ? err.message : String(err) }));
    } finally {
      setBusy(false);
      abort.current = null;
    }
  }

  useEffect(() => {
    if (pending && !busy) {
      onPendingTaken();
      void ask(pending);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pending, busy]);

  const submit = (e: FormEvent) => { e.preventDefault(); void ask(input); };
  const reset = () => { abort.current?.abort(); setMsgs([]); setSession(newSessionId()); };

  return (
    <aside className="ask">
      <header className="ask-head">
        <div>
          <h2>Ask the FinOps agent</h2>
          <p className="muted">Charts below come straight from the agent's cost tools.</p>
        </div>
        {msgs.length > 0 && <button className="link" onClick={reset}>New conversation</button>}
      </header>

      <div className="ask-log">
        {msgs.length === 0 && (
          <div className="suggestions">
            {SUGGESTIONS.map(s => <button key={s} className="suggestion" onClick={() => void ask(s)}>{s}</button>)}
          </div>
        )}
        {msgs.map((m, i) => m.role === 'user'
          ? <div key={i} className="msg user">{m.text}</div>
          : (
            <div key={i} className="msg assistant">
              {m.parts.map((p, j) => p.kind === 'text'
                ? <div key={j} className="md"><Markdown remarkPlugins={[remarkGfm]}>{p.text}</Markdown></div>
                : (
                  <div key={p.id} className="tool">
                    <div className={`step ${p.done ? 'done' : ''}`}>
                      <span className="step-dot" aria-hidden />{TOOL_LABELS[p.name] ?? p.name}
                      <code>{Object.entries(p.input).filter(([, v]) => v !== '' && v != null).map(([k, v]) => `${k}=${String(v)}`).join(' ')}</code>
                    </div>
                    {p.done && <ToolResultView name={p.name} result={p.result} />}
                  </div>
                ))}
              {!m.done && m.parts.length === 0 && <div className="step"><span className="step-dot" aria-hidden />Thinking…</div>}
              {m.error && <div className="banner error">{m.error}</div>}
            </div>
          ))}
        <div ref={end} />
      </div>

      <form className="ask-input" onSubmit={submit}>
        <textarea value={input} onChange={e => setInput(e.target.value)} rows={2} maxLength={8000}
                  placeholder="e.g. Which service grew the most this week?"
                  onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); void ask(input); } }} />
        {busy
          ? <button type="button" className="btn" onClick={() => abort.current?.abort()}>Stop</button>
          : <button type="submit" className="btn primary" disabled={!input.trim()}>Ask</button>}
      </form>
    </aside>
  );
}
