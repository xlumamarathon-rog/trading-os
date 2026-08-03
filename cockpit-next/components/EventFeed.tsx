'use client';
import type { TradeEvent } from '@/lib/types';
export default function EventFeed({ events }: { events: TradeEvent[] }) {
  return (
    <div className="card">
      <h3>Event feed <span className="dim">(anomalies · regime · audit)</span></h3>
      <div className="list mono">
        {events.map((e, i) => (
          <div key={i} className={`row ev ${e.level}`}>
            <span className="t">{e.t}</span><span>{e.m}</span></div>))}
      </div>
    </div>
  );
}
