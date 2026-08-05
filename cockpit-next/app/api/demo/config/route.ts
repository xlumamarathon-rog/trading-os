import { NextResponse } from 'next/server';
export const dynamic = 'force-dynamic';
export async function GET() {
  return NextResponse.json({
    risk_limits: { max_risk_per_trade_pct: 0.01, max_position_pct: 0.05 },
    exit_manager: { breakeven_at_r: 1.0, never_widen_stop: true },
  });
}
