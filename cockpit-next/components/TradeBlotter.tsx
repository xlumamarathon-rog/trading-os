'use client';
import { useEffect, useState } from 'react';
import { api } from '@/lib/api';

type Trade = { symbol: string; direction?: string; realized_r?: number;
               reason?: string; mfe_captured_pct?: number };

export default function TradeBlotter({ token }: { token: string }) {
  const [trades, setTrades] = useState<Trade[]>([]);

  useEffect(() => {
    if (!token) return;
    let alive = true;
    const load = () => api.trades(token).then((t) => alive && setTrades(t)).catch(() => {});
    load();
    const id = setInterval(load, 12000);
    return () => { alive = false; clearInterval(id); };
  }, [token]);

  return (
    <section className="card">
      <h3>Trade blotter <span className="dim">(closed trades · R · exit reason · MFE captured)</span></h3>
      <table>
        <thead><tr><th>Symbol</th><th>Side</th><th>R</th><th>Exit reason</th><th>MFE cap %</th></tr></thead>
        <tbody>
          {trades.length === 0 && (
            <tr><td colSpan={5} className="dim">no closed trades yet</td></tr>)}
          {trades.slice(-25).reverse().map((t, i) => (
            <tr key={i}>
              <td>{t.symbol}</td>
              <td>{t.direction || ''}</td>
              <td className={(t.realized_r ?? 0) >= 0 ? 'pos' : 'neg'}>
                {(t.realized_r ?? 0).toFixed(2)}R</td>
              <td>{t.reason}</td>
              <td>{t.mfe_captured_pct ?? ''}</td>
            </tr>))}
        </tbody>
      </table>
    </section>
  );
}
