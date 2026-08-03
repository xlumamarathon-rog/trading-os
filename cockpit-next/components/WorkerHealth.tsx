'use client';
import type { WorkerHealth } from '@/lib/types';
export default function WorkerHealthChips({ workers }: { workers: WorkerHealth }) {
  const entries = Object.entries(workers);
  return (
    <div className="workers">
      {entries.length === 0 && <span className="dim">—</span>}
      {entries.map(([name, ok]) => (
        <span key={name} className={`chip ${ok ? '' : 'dead'}`}>{name}{ok ? '' : ' ✗'}</span>))}
    </div>
  );
}
