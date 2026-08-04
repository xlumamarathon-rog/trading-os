import { NextResponse } from 'next/server';
export async function POST() { return NextResponse.json({ entries_resumed: true }); }
