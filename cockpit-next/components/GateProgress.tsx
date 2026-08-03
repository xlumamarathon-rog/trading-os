'use client';
import type { CockpitState } from '@/lib/types';
export default function GateProgress({ gate }: { gate: CockpitState['gate'] }) {
  const checks = [
    { label: `Paper days ${gate.paper_days_completed}/14`, ok: gate.paper_days_completed >= 14 },
    { label: `Clean recon streak ${gate.clean_reconciliation_streak}/5`, ok: gate.clean_reconciliation_streak >= 5 },
    { label: 'SEBI Feb-2025 checks', ok: gate.sebi_checks_passed },
    { label: 'Static IP confirmed', ok: gate.static_ip },
    { label: 'Human ack phrase', ok: gate.human_ack },
  ];
  const passed = checks.filter((c) => c.ok).length;
  return (
    <div className="card">
      <h3>Live-mode gate <span className="dim">({passed}/{checks.length} — live blocked until all pass)</span></h3>
      <div className="gate-grid">
        {checks.map((c) => (
          <div key={c.label} className={`gate-item ${c.ok ? 'ok' : 'pending'}`}>
            <span>{c.ok ? '✓' : '○'}</span> {c.label}</div>))}
      </div>
    </div>
  );
}
