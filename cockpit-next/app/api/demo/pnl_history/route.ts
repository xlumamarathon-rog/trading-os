import { NextResponse } from 'next/server';
export const dynamic = 'force-dynamic';
export async function GET() {
  return NextResponse.json([
    { date: '2026-06-30', equity: 1000000 }, { date: '2026-07-15', equity: 1004200 },
    { date: '2026-07-31', equity: 1002340 }, { date: '2026-08-04', equity: 1006100 },
  ]);
}
