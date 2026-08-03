import { NextResponse } from 'next/server';
import { demoState } from '@/lib/demo';
export const dynamic = 'force-dynamic';
export async function GET() { return NextResponse.json(demoState()); }
