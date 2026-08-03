'use client';
import { api } from '@/lib/api';
import type { Approval } from '@/lib/types';
export default function ApprovalsInbox({ token, approvals, role, onDone }:
    { token: string; approvals: Approval[]; role: string; onDone: () => void }) {
  return (
    <div className="card grow">
      <h3>Approvals inbox <span className="dim">(rules &amp; model promotions — human gate)</span></h3>
      <div className="list">
        {approvals.length === 0 && <div className="dim">nothing pending — the gates are quiet</div>}
        {approvals.map((a) => (
          <div key={a.id} className="row">
            <span className={`tag ${a.kind}`}>{a.kind}</span>
            <span>{a.label}</span>
            {role === 'operator' && (
              <button className="ghost small approve"
                onClick={async () => { await api.approve(token, a.id); onDone(); }}>APPROVE</button>)}
          </div>))}
      </div>
    </div>
  );
}
