'use client';
import { useState } from 'react';
import { api } from '@/lib/api';

export default function KillSwitch({ token, halted, role, onDone }:
    { token: string; halted: boolean; role: string; onDone: () => void }) {
  const [confirming, setConfirming] = useState(false);
  const [phrase, setPhrase] = useState('');
  const [reason, setReason] = useState('');
  const [unlockPhrase, setUnlockPhrase] = useState('');
  const [err, setErr] = useState('');
  const op = role === 'operator';

  async function doKill() {
    if (phrase !== 'KILL ALL POSITIONS') { setErr('phrase must match exactly'); return; }
    try { await api.kill(token, reason); setConfirming(false); setPhrase(''); setReason(''); setErr(''); onDone(); }
    catch (e: any) { setErr(e.message); }
  }
  async function doUnlock() {
    try { await api.unlock(token, unlockPhrase); setUnlockPhrase(''); setErr(''); onDone(); }
    catch (e: any) { setErr('unlock refused'); }
  }

  return (
    <div className="card danger-card">
      <h3>Emergency</h3>
      {!halted && !confirming && (
        <button className="kill" disabled={!op} onClick={() => setConfirming(true)}>
          {op ? 'KILL SWITCH' : 'KILL SWITCH (operator only)'}</button>)}
      {!halted && confirming && (
        <div>
          <p className="dim">Type <code>KILL ALL POSITIONS</code> to confirm:</p>
          <input value={phrase} onChange={(e) => setPhrase(e.target.value)} placeholder="confirmation phrase" />
          <input value={reason} onChange={(e) => setReason(e.target.value)} placeholder="reason (logged)" />
          <button className="kill small" onClick={doKill}>CONFIRM KILL</button>
          <button className="ghost small" onClick={() => setConfirming(false)}>cancel</button>
        </div>)}
      {halted && (
        <div>
          <div className="halt-pill">⛔ HALTED</div>
          {op && (<>
            <p className="dim">Enter unlock phrase:</p>
            <input type="password" value={unlockPhrase}
              onChange={(e) => setUnlockPhrase(e.target.value)} />
            <button className="ghost small" onClick={doUnlock}>UNLOCK</button>
          </>)}
        </div>)}
      {err && <div className="err-line">{err}</div>}
    </div>
  );
}
