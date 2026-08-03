'use client';
import { useEffect, useRef } from 'react';
import { createChart, ColorType, IChartApi, UTCTimestamp } from 'lightweight-charts';

export default function EquityChart({ data }: { data: { time: number; value: number }[] }) {
  const ref = useRef<HTMLDivElement>(null);
  const seriesRef = useRef<any>(null);
  useEffect(() => {
    if (!ref.current) return;
    const chart: IChartApi = createChart(ref.current, {
      layout: { background: { type: ColorType.Solid, color: '#11151f' }, textColor: '#6b7689' },
      grid: { vertLines: { color: '#151b28' }, horzLines: { color: '#151b28' } },
      timeScale: { borderColor: '#1d2433', timeVisible: true },
      rightPriceScale: { borderColor: '#1d2433' }, height: 220, autoSize: true,
    });
    seriesRef.current = chart.addAreaSeries({
      lineColor: '#4aa3ff', topColor: 'rgba(74,163,255,0.35)', bottomColor: 'rgba(74,163,255,0.02)',
      lineWidth: 2 });
    return () => chart.remove();
  }, []);
  useEffect(() => {
    if (!seriesRef.current) return;
    seriesRef.current.setData(data.map((d) => ({ time: d.time as UTCTimestamp, value: d.value })));
  }, [data]);
  return <div ref={ref} className="chart-body" />;
}
