import { NextResponse } from 'next/server';
export const dynamic = 'force-dynamic';
export async function GET() {
  return NextResponse.json([
    { symbol: 'RELIANCE', direction: 'buy', realized_r: 1.8, reason: 'trail_stop', mfe_captured_pct: 78.3, sleeve: 'tsmom_f' },
    { symbol: 'BTCUSD', direction: 'sell', realized_r: -0.9, reason: 'stop_hit', mfe_captured_pct: 0.0, sleeve: 'tsmom_f' },
    { symbol: 'EURUSD', direction: 'buy', realized_r: 0.4, reason: 'time_stop_no_progress', mfe_captured_pct: 31.0, sleeve: 'accurate' },
  ]);
}
