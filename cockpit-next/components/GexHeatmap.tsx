'use client';
import type { GexStrike } from '@/lib/types';
export default function GexHeatmap({ strikes, net, regime }:
    { strikes: GexStrike[]; net: number; regime: string }) {
  const max = Math.max(...strikes.map((s) => Math.abs(s.gex)), 1);
  return (
    <div>
      <div className="dim" style={{ marginBottom: 8 }}>
        net GEX <b style={{ color: net >= 0 ? '#2ecc71' : '#e74c3c' }}>
          {(net / 1e6).toFixed(1)}M</b> · dealers{' '}
        <b style={{ color: regime === 'amplify' ? '#e74c3c' : '#2ecc71' }}>{regime}</b> moves</div>
      <div className="gex-grid">
        {strikes.map((s) => {
          const w = (Math.abs(s.gex) / max) * 100;
          return (
            <div key={s.strike} className="gex-row">
              <span className="gex-strike">{s.strike}</span>
              <div className="gex-bar-wrap">
                <div className="gex-bar" style={{
                  width: `${w}%`, marginLeft: s.gex < 0 ? `${100 - w}%` : '0',
                  background: s.gex >= 0 ? '#2ecc71' : '#e74c3c' }} />
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
