export type ExitState = 'RISK_ON' | 'BREAKEVEN' | 'TRAILING' | 'EXITED';
export type Leg = 'india' | 'mt5_forex' | 'mt5_crypto';

export interface Position {
  symbol: string; leg: Leg; qty: number; entry: number; stop: number;
  r_now: number; state: ExitState; mfe_r: number; unrealized: number;
}
export interface Candle { time: number; open: number; high: number; low: number; close: number; }
export interface WorkerHealth { [name: string]: boolean; }
export interface Approval { id: string; label: string; kind: 'rule' | 'model'; }
export interface TradeEvent { t: string; m: string; level: 'info' | 'warn' | 'alert'; }
export interface GexStrike { strike: number; gex: number; }
export interface CockpitState {
  mode: 'paper' | 'live';
  halted: boolean;
  equity: number; pnl: number; costs: number;
  var95: number; varLimit: number;
  role: 'viewer' | 'operator';
  positions: Position[];
  equityCurve: { time: number; value: number }[];
  candles: Record<string, Candle[]>;
  workers: WorkerHealth;
  approvals: Approval[];
  events: TradeEvent[];
  gex: { net: number; regime: 'amplify' | 'dampen'; strikes: GexStrike[] };
  gate: { paper_days_completed: number; clean_reconciliation_streak: number;
          sebi_checks_passed: boolean; static_ip: boolean; human_ack: boolean };
}
