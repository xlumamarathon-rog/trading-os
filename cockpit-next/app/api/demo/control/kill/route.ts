import { NextResponse } from 'next/server';
import { demoKill } from '@/lib/demo';
export async function POST(req: Request) {
  const body = await req.json();
  if (body.confirm !== 'KILL ALL POSITIONS')
    return NextResponse.json({ detail: 'confirmation phrase required' }, { status: 400 });
  demoKill();
  return NextResponse.json({ orders_cancelled: ['demo:S1', 'demo:S2'],
                             positions_closed: ['RELIANCE', 'BTCUSD', 'EURUSD'] });
}
