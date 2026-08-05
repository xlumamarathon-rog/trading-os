'use client';
import { useEffect, useState } from 'react';
import { api } from '@/lib/api';

const fmt = (x: number) =>
  (x < 0 ? '-' : '') + '₹' + Math.abs(x).toLocaleString('en-IN', { maximumFractionDigits: 0 });

export default function PnlPanels({ token }: { token: string }) {
  const [hist, setHist] = useState<{ date: string; equity: number }[]>([]);
  const [trades, setTrades] = useState<any[]>([]);
  const [cfg, setCfg] = useState<any>(null);

  useEffect(() => {
    if (!token) return;
    let alive = true;
    const load = () => {
      api.pnlHistory(token).then((h) => alive && setHist(h)).catch(() => {});
      api.trades(token).then((t) => alive && setTrades(t)).catch(() => {});
    };
    load();
    api.configView(token).then((c) => alive && setCfg(c)).catch(() => {});
    const id = setInterval(load, 30000);
    return () => { alive = false; clearInterval(id); };
  }, [token]);

  // monthly rollup from daily equity closes
  const byMonth = new Map<string, { first: number; last: number }>();
  for (const p of hist) {
    const m = p.date.slice(0, 7);
    if (!byMonth.has(m)) byMonth.set(m, { first: p.equity, last: p.equity });
    byMonth.get(m)!.last = p.equity;
  }
  let prevEnd: number | null = null;
  const months = [...byMonth.entries()].map(([m, v]) => {
    const start = prevEnd ?? v.first;
    const pnl = v.last - start;
    prevEnd = v.last;
    return { m, pnl, ret: start ? (pnl / start) * 100 : 0, end: v.last };
  });

  // sleeve attribution from the blotter
  const agg = new Map<string, { r: number; n: number; w: number }>();
  for (const t of trades) {
    const k = t.sleeve || 'unattributed';
    const a = agg.get(k) || { r: 0, n: 0, w: 0 };
    a.r += t.realized_r ?? 0; a.n += 1; if ((t.realized_r ?? 0) > 0) a.w += 1;
    agg.set(k, a);
  }

  return (
    <>
      <section className="grid-2">
        <div className="card">
          <h3>P&amp;L calendar <span className="dim">(monthly)</span></h3>
          <table>
            <thead><tr><th>Month</th><th>P&amp;L</th><th>Return</th><th>End equity</th></tr></thead>
            <tbody>
              {months.length === 0 && <tr><td colSpan={4} className="dim">no history yet</td></tr>}
              {months.map((r) => (
                <tr key={r.m}>
                  <td>{r.m}</td>
                  <td className={r.pnl >= 0 ? 'pos' : 'neg'}>{r.pnl >= 0 ? '+' : ''}{fmt(r.pnl)}</td>
                  <td className={r.pnl >= 0 ? 'pos' : 'neg'}>{r.ret.toFixed(2)}%</td>
                  <td>{fmt(r.end)}</td>
                </tr>))}
            </tbody>
          </table>
        </div>
        <div className="card">
          <h3>Sleeve attribution <span className="dim">(realized R by strategy)</span></h3>
          <table>
            <thead><tr><th>Sleeve</th><th>Total R</th><th>Trades</th><th>Win rate</th></tr></thead>
            <tbody>
              {agg.size === 0 && <tr><td colSpan={4} className="dim">no closed trades yet</td></tr>}
              {[...agg.entries()].map(([k, a]) => (
                <tr key={k}>
                  <td>{k}</td>
                  <td className={a.r >= 0 ? 'pos' : 'neg'}>{a.r >= 0 ? '+' : ''}{a.r.toFixed(2)}R</td>
                  <td>{a.n}</td>
                  <td>{a.n ? Math.round((a.w / a.n) * 100) : 0}%</td>
                </tr>))}
            </tbody>
          </table>
        </div>
      </section>
      <section className="card">
        <details>
          <summary><b>Running config</b> <span className="dim">(sanitized, read-only)</span></summary>
          <pre style={{ overflowX: 'auto' }}>{cfg ? JSON.stringify(cfg, null, 2) : 'unavailable'}</pre>
        </details>
      </section>
    </>
  );
}
