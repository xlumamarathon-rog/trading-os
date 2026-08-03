import { NextResponse } from 'next/server';
import { demoUnlock } from '@/lib/demo';
export async function POST(req: Request) {
  const body = await req.json();
  const ok = demoUnlock(body.confirm);
  if (!ok) return NextResponse.json({ detail: 'wrong unlock phrase' }, { status: 403 });
  return NextResponse.json({ halted: false });
}
