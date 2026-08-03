'use client';
import type { Position } from '@/lib/types';
export default function PositionsTable({ positions }: { positions: Position[] }) {
  return (
    <div className="card">
      <h3>Open positions · exit states</h3>
      <table>
        <thead><tr><th>Symbol</th><th>Leg</th><th>Qty</th><th>Entry</th><th>Stop</th>
          <th>R now</th><th>State</th><th>MFE</th><th>Unreal.</th></tr></thead>
        <tbody>
          {positions.length === 0 && <tr><td colSpan={9} className="dim">no open positions</td></tr>}
          {positions.map((p) => (
            <tr key={p.symbol}>
              <td>{p.symbol}</td><td>{p.leg}</td><td>{p.qty}</td>
              <td>{p.entry}</td><td>{p.stop.toFixed(2)}</td>
              <td className={p.r_now >= 0 ? 'pos' : 'neg'}>{p.r_now.toFixed(1)}R</td>
              <td><span className={`state ${p.state}`}>{p.state}</span></td>
              <td>{p.mfe_r.toFixed(1)}R</td>
              <td className={p.unrealized >= 0 ? 'pos' : 'neg'}>{p.unrealized.toFixed(0)}</td>
            </tr>))}
        </tbody>
      </table>
    </div>
  );
}
