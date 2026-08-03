import type { CockpitState } from './types';

// The cockpit talks ONLY to the M44 gateway. In DEMO mode it uses the built-in
// Next.js mock route (/api/demo/*). Set NEXT_PUBLIC_GATEWAY_URL to point at the
// real Python gateway. ZERO order logic lives client-side (spec §12.11):
// this module reads state and POSTs authenticated control INTENTS, nothing else.

const GATEWAY = process.env.NEXT_PUBLIC_GATEWAY_URL || '';
const DEMO = !GATEWAY;

function base(path: string) {
  return DEMO ? `/api/demo${path}` : `${GATEWAY}${path}`;
}

async function req(path: string, token: string, opts: RequestInit = {}) {
  const res = await fetch(base(path), {
    ...opts,
    headers: {
      'Content-Type': 'application/json',
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...(opts.headers || {}),
    },
    cache: 'no-store',
  });
  if (res.status === 401) throw new Error('auth');
  if (res.status === 403) throw new Error('forbidden');
  if (!res.ok) throw new Error(`http_${res.status}`);
  return res.json();
}

export const api = {
  isDemo: DEMO,
  getState: (token: string): Promise<CockpitState> => req('/state', token),
  kill: (token: string, reason: string) =>
    req('/control/kill', token, { method: 'POST',
      body: JSON.stringify({ confirm: 'KILL ALL POSITIONS', reason }) }),
  unlock: (token: string, phrase: string) =>
    req('/control/unlock', token, { method: 'POST',
      body: JSON.stringify({ confirm: phrase }) }),
  pause: (token: string, reason: string) =>
    req('/control/pause_entries', token, { method: 'POST',
      body: JSON.stringify({ reason }) }),
  approve: (token: string, id: string) =>
    req(`/control/approve/${id}`, token, { method: 'POST', body: '{}' }),
};
