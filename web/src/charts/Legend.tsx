export function Legend({ items }: { items: { key: string; label: string; color: string }[] }) {
  if (items.length < 2) return null;
  return (
    <ul className="legend">
      {items.map(i => (
        <li key={i.key}>
          <span className="swatch" style={{ background: i.color }} />
          {i.label}
        </li>
      ))}
    </ul>
  );
}
