import { useState, type ReactNode } from 'react';

export interface TableData {
  columns: string[];
  rows: (string | number)[][];
}

/** A titled card with a chart and its table view (every value stays reachable without hovering). */
export function ChartCard({ title, subtitle, table, actions, children, className = '' }: {
  title: string; subtitle?: ReactNode; table?: TableData; actions?: ReactNode; children: ReactNode; className?: string;
}) {
  const [view, setView] = useState<'chart' | 'table'>('chart');
  return (
    <section className={`card ${className}`}>
      <header className="card-head">
        <div>
          <h3>{title}</h3>
          {subtitle && <p className="card-sub">{subtitle}</p>}
        </div>
        <div className="card-actions">
          {actions}
          {table && (
            <div className="seg" role="group" aria-label="View">
              <button className={view === 'chart' ? 'on' : ''} onClick={() => setView('chart')}>Chart</button>
              <button className={view === 'table' ? 'on' : ''} onClick={() => setView('table')}>Table</button>
            </div>
          )}
        </div>
      </header>
      {view === 'table' && table ? <DataTable {...table} /> : children}
    </section>
  );
}

export function DataTable({ columns, rows }: TableData) {
  return (
    <div className="table-wrap">
      <table className="data">
        <thead><tr>{columns.map(c => <th key={c}>{c}</th>)}</tr></thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={i}>{r.map((c, j) => <td key={j} className={typeof c === 'number' ? 'num' : ''}>{c}</td>)}</tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
