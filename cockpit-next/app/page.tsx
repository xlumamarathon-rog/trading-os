'use client';
import { useCallback, useEffect, useState } from 'react';
import { api } from '@/lib/api';
import type { CockpitState } from '@/lib/types';
import PriceChart from '@/components/PriceChart';
import EquityChart from '@/components/EquityChart';
import VarGauge from '@/components/VarGauge';
import GexHeatmap from '@/components/GexHeatmap';
import KillSwitch from '@/components/KillSwitch';
import PositionsTable from '@/components/PositionsTable';
import ApprovalsInbox from '@/components/ApprovalsInbox';
import WorkerHealthChips from '@/components/WorkerHealth';
import EventFeed from '@/components/EventFeed';
import GateProgress from '@/components/GateProgress';

const fmt = (x: number) =>
  (x < 0 ? '-' : '') + '₹' + Math.abs(x).toLocaleString('en-IN', { maximumFractionDigits: 0 });

export default function Cockpit() {
  const [token, setToken] = useState('');
  const [state, setState] = useState<CockpitState | null>(null);
  const [conn, setConn] = useState('connecting…');
  const [connOk, setConnOk] = useState(false);

  useEffect(() => { setToken(localStorage.getItem('cockpit_token') || (api.isDemo ? 'demo' : '')); }, []);

  const refresh = useCallback(async () => {
    try {
      const s = await api.getState(token);
      setState(s); setConn(api.isDemo ? 'demo mode — mock data' : 'live'); setConnOk(true);
    } catch (e: any) {
      setConn(e.message === 'auth' ? 'invalid token' : 'gateway unreachable'); setConnOk(false);
    }
  }, [token]);

  useEffect(() => {
    if (!token) return;
    refresh();
    const id = setInterval(refresh, 3000);
    return () => clearInterval(id);
  }, [token, refresh]);

  const onToken = (v: string) => { setToken(v); localStorage.setItem('cockpit_token', v); };

  return (
    <>
      <header>
        <div className="brand">TRADING<span>OS</span></div>
        {state && <div className={`badge ${state.mode}`}>{state.mode.toUpperCase()}</div>}
        {state?.halted && <div className="halt-banner">⛔ TRADING HALTED</div>}
        <div className="auth">
          <input type="password" value={token} placeholder="access token"
            onChange={(e) => onToken(e.target.value)} />
          <span className="role">{state?.role || '—'}</span>
        </div>
      </header>

      {!state && <main><div className="card">Connecting to gateway… <span className="dim">{conn}</span></div></main>}

      {state && (
        <main>
          <section className="cards">
            <div className="card"><h3>Equity</h3><div className="big">{fmt(state.equity)}</div></div>
            <div className="card"><h3>Day P&amp;L</h3>
              <div className={`big ${state.pnl >= 0 ? 'pos' : 'neg'}`}>
                {state.pnl >= 0 ? '+' : ''}{fmt(state.pnl)}</div>
              <div className="dim">costs {fmt(state.costs)}</div></div>
            <div className="card"><h3>VaR 95 vs limit</h3>
              <VarGauge var95={state.var95} limit={state.varLimit} /></div>
            <div className="card"><h3>Workers</h3><WorkerHealthChips workers={state.workers} /></div>
          </section>

          <section className="card"><h3>Equity curve</h3><EquityChart data={state.equityCurve} /></section>

          <section className="grid-charts">
            {Object.entries(state.candles).map(([sym, c]) => (
              <PriceChart key={sym} symbol={sym} candles={c} />))}
          </section>

          <section className="grid-2">
            <div className="card"><h3>Dealer gamma (GEX) map</h3>
              <GexHeatmap strikes={state.gex.strikes} net={state.gex.net} regime={state.gex.regime} /></div>
            <GateProgress gate={state.gate} />
          </section>

          <section className="control-row">
            <KillSwitch token={token} halted={state.halted} role={state.role} onDone={refresh} />
            <ApprovalsInbox token={token} approvals={state.approvals} role={state.role} onDone={refresh} />
          </section>

          <PositionsTable positions={state.positions} />
          <EventFeed events={state.events} />
        </main>
      )}

      <footer>
        <span className={`conn ${connOk ? 'ok' : 'err'}`}>{conn}</span>
        <span className="dim">viewer sees state · operator controls · every control audited (spec §12.11)</span>
      </footer>
    </>
  );
}
