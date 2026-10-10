import type { ReactNode } from 'react';

export function StatTile({ label, value, detail, hero = false }: {
  label: string; value: ReactNode; detail?: ReactNode; hero?: boolean;
}) {
  return (
    <div className={`tile ${hero ? 'hero' : ''}`}>
      <div className="tile-label">{label}</div>
      <div className="tile-value">{value}</div>
      {detail && <div className="tile-detail">{detail}</div>}
    </div>
  );
}
