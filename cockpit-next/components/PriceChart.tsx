'use client';
import { useEffect, useRef } from 'react';
import { createChart, ColorType, IChartApi, CandlestickData, UTCTimestamp } from 'lightweight-charts';
import type { Candle } from '@/lib/types';

export default function PriceChart({ symbol, candles }: { symbol: string; candles: Candle[] }) {
  const ref = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const seriesRef = useRef<any>(null);

  useEffect(() => {
    if (!ref.current) return;
    const chart = createChart(ref.current, {
      layout: { background: { type: ColorType.Solid, color: '#11151f' }, textColor: '#6b7689' },
      grid: { vertLines: { color: '#1d2433' }, horzLines: { color: '#1d2433' } },
      timeScale: { borderColor: '#1d2433', timeVisible: true },
      rightPriceScale: { borderColor: '#1d2433' },
      height: 260, autoSize: true,
    });
    const series = chart.addCandlestickSeries({
      upColor: '#2ecc71', downColor: '#e74c3c', borderVisible: false,
      wickUpColor: '#2ecc71', wickDownColor: '#e74c3c',
    });
    chartRef.current = chart; seriesRef.current = series;
    return () => chart.remove();
  }, []);

  useEffect(() => {
    if (!seriesRef.current) return;
    const data: CandlestickData[] = candles.map((c) => ({
      time: c.time as UTCTimestamp, open: c.open, high: c.high, low: c.low, close: c.close }));
    seriesRef.current.setData(data);
  }, [candles]);

  return (
    <div className="chart-card">
      <div className="chart-head"><span>{symbol}</span>
        <span className="dim">{candles.length ? candles[candles.length - 1].close.toFixed(2) : '—'}</span></div>
      <div ref={ref} className="chart-body" />
    </div>
  );
}
