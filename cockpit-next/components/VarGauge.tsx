'use client';
export default function VarGauge({ var95, limit }: { var95: number; limit: number }) {
  const pct = Math.min(100, (var95 / limit) * 100);
  const color = pct < 60 ? '#2ecc71' : pct < 90 ? '#f39c12' : '#e74c3c';
  return (
    <div>
      <div className="gauge"><div className="gauge-fill" style={{ width: `${pct}%`, background: color }} /></div>
      <div className="dim" style={{ marginTop: 6 }}>
        {(var95 * 100).toFixed(2)}% of {(limit * 100).toFixed(1)}% limit</div>
    </div>
  );
}
