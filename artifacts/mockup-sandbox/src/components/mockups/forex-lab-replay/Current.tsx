import './_group.css';
import { type PointerEvent, type WheelEvent, useEffect, useMemo, useState } from 'react';
import { Activity, ArrowDownRight, ArrowUpRight, ChevronLeft, ChevronRight, Crosshair, Database, Minus, Plus, RotateCcw } from 'lucide-react';

type Timeframe = 'M1' | 'M5' | 'M15';
type Candle = { time: string; open: number; high: number; low: number; close: number };
type Alert = { alertId: string; time: string; direction: 'BUY' | 'SELL'; strengthLabel: 'STRONG' | 'NEUTRAL' | 'WEAK'; cct: number; cst: number; timeframe: Timeframe; candle: Candle };
type BacktestResult = {
  timeframe: Timeframe; from: string; to: string; candles: Candle[]; alerts: Alert[];
  counts: { buy: number; sell: number; strong: number; neutral: number; weak: number };
  truncated: boolean; totalCandles: number; runId: string; durationMs: number; specVersion: string;
};

// Exact approved XAUUSD M5 sample supplied for this extraction.
const fixture: Candle[] = [
  ['2025-01-31T16:40:00Z', 2807.765, 2809.455, 2806.385, 2807.095],
  ['2025-01-31T16:45:00Z', 2807.095, 2807.425, 2805.825, 2806.385],
  ['2025-01-31T16:50:00Z', 2806.265, 2808.325, 2805.665, 2808.165],
  ['2025-01-31T16:55:00Z', 2808.235, 2808.315, 2805.695, 2807.295],
  ['2025-01-31T17:00:00Z', 2807.315, 2809.395, 2807.095, 2808.925],
  ['2025-01-31T17:05:00Z', 2808.965, 2809.815, 2805.305, 2806.355],
  ['2025-01-31T17:10:00Z', 2806.355, 2807.295, 2802.895, 2806.295],
  ['2025-01-31T17:15:00Z', 2806.128, 2812.655, 2803.405, 2810.835],
  ['2025-01-31T17:20:00Z', 2810.875, 2811.948, 2807.975, 2808.848],
  ['2025-01-31T17:25:00Z', 2808.795, 2809.248, 2805.915, 2807.715],
  ['2025-01-31T17:30:00Z', 2807.715, 2809.885, 2806.965, 2808.778],
  ['2025-01-31T17:35:00Z', 2808.698, 2812.495, 2808.118, 2812.305],
  ['2025-01-31T17:40:00Z', 2812.295, 2816.088, 2812.225, 2813.295],
  ['2025-01-31T17:45:00Z', 2813.598, 2815.978, 2811.015, 2812.665],
  ['2025-01-31T17:50:00Z', 2812.665, 2813.065, 2809.185, 2810.178],
  ['2025-01-31T17:55:00Z', 2810.048, 2810.435, 2807.865, 2808.475],
  ['2025-01-31T18:00:00Z', 2808.468, 2808.875, 2806.215, 2806.298],
  ['2025-01-31T18:05:00Z', 2806.578, 2808.248, 2805.538, 2806.915],
  ['2025-01-31T18:10:00Z', 2806.925, 2807.675, 2805.015, 2805.165],
  ['2025-01-31T18:15:00Z', 2805.058, 2805.448, 2801.148, 2801.675],
  ['2025-01-31T18:20:00Z', 2801.675, 2804.448, 2798.775, 2802.575],
  ['2025-01-31T18:25:00Z', 2802.568, 2802.618, 2798.215, 2799.565],
  ['2025-01-31T18:30:00Z', 2799.565, 2799.655, 2795.075, 2796.315],
  ['2025-01-31T18:35:00Z', 2796.285, 2801.515, 2796.095, 2800.995],
  ['2025-01-31T18:40:00Z', 2801.005, 2803.065, 2799.925, 2802.095],
  ['2025-01-31T18:45:00Z', 2802.105, 2802.295, 2799.265, 2800.335],
  ['2025-01-31T18:50:00Z', 2800.415, 2800.765, 2797.445, 2798.595],
  ['2025-01-31T18:55:00Z', 2798.595, 2799.385, 2797.775, 2798.685],
  ['2025-01-31T19:00:00Z', 2798.725, 2798.945, 2797.425, 2797.885],
  ['2025-01-31T19:05:00Z', 2797.915, 2798.785, 2796.735, 2797.995],
  ['2025-01-31T19:10:00Z', 2798.085, 2800.925, 2797.295, 2800.775],
  ['2025-01-31T19:15:00Z', 2800.765, 2800.885, 2798.765, 2799.555],
  ['2025-01-31T19:20:00Z', 2799.535, 2801.415, 2799.465, 2801.215],
  ['2025-01-31T19:25:00Z', 2801.235, 2801.675, 2800.325, 2801.605],
  ['2025-01-31T19:30:00Z', 2801.585, 2802.595, 2799.795, 2800.435],
  ['2025-01-31T19:35:00Z', 2800.458, 2800.825, 2799.215, 2800.115],
  ['2025-01-31T19:40:00Z', 2800.055, 2800.185, 2795.898, 2796.428],
  ['2025-01-31T19:45:00Z', 2796.225, 2797.568, 2795.118, 2797.425],
  ['2025-01-31T19:50:00Z', 2797.425, 2797.965, 2796.165, 2797.215],
  ['2025-01-31T19:55:00Z', 2797.175, 2797.495, 2794.818, 2795.905],
  ['2025-01-31T20:00:00Z', 2795.955, 2796.108, 2793.965, 2794.565],
  ['2025-01-31T20:05:00Z', 2794.545, 2796.375, 2794.305, 2796.245],
  ['2025-01-31T20:10:00Z', 2796.285, 2797.395, 2795.975, 2797.065],
  ['2025-01-31T20:15:00Z', 2797.065, 2797.365, 2795.135, 2796.455],
  ['2025-01-31T20:20:00Z', 2796.435, 2796.985, 2795.815, 2796.705],
  ['2025-01-31T20:25:00Z', 2796.725, 2797.055, 2795.865, 2796.835],
  ['2025-01-31T20:30:00Z', 2796.865, 2797.325, 2795.898, 2796.385],
  ['2025-01-31T20:35:00Z', 2796.265, 2797.475, 2796.185, 2797.285],
  ['2025-01-31T20:40:00Z', 2797.265, 2798.685, 2796.378, 2798.655],
  ['2025-01-31T20:45:00Z', 2798.635, 2800.175, 2798.415, 2799.975],
  ['2025-01-31T20:50:00Z', 2799.975, 2800.938, 2798.898, 2799.855],
  ['2025-01-31T20:55:00Z', 2799.848, 2801.988, 2798.958, 2800.698],
  ['2025-01-31T21:00:00Z', 2800.738, 2801.555, 2798.808, 2801.505],
  ['2025-01-31T21:05:00Z', 2801.495, 2802.035, 2800.125, 2800.998],
  ['2025-01-31T21:10:00Z', 2800.998, 2801.558, 2799.498, 2801.165],
  ['2025-01-31T21:15:00Z', 2801.518, 2802.295, 2801.008, 2801.428],
  ['2025-01-31T21:20:00Z', 2801.508, 2801.958, 2800.498, 2801.135],
  ['2025-01-31T21:25:00Z', 2801.308, 2801.308, 2799.888, 2800.858],
  ['2025-01-31T21:30:00Z', 2800.928, 2801.178, 2800.128, 2800.868],
  ['2025-01-31T21:35:00Z', 2800.708, 2801.038, 2798.495, 2798.825],
  ['2025-01-31T21:40:00Z', 2798.825, 2799.505, 2798.135, 2799.465],
  ['2025-01-31T21:45:00Z', 2799.405, 2800.785, 2799.405, 2800.705],
  ['2025-01-31T21:50:00Z', 2800.735, 2801.925, 2799.545, 2799.825],
  ['2025-01-31T21:55:00Z', 2800.075, 2800.845, 2796.605, 2798.085],
].map(([time, open, high, low, close]) => ({
  time: time as string, open: open as number, high: high as number, low: low as number, close: close as number,
}));

const dataset = {
  source: 'Approved XAUUSD M5 fixture',
  startDate: fixture[0].time,
  endDate: fixture.at(-1)!.time,
  candleCount: fixture.length,
  availableTimeframes: ['M5'] as Timeframe[],
  sourceSha256: 'Not supplied with fixture',
};

const formatDateTime = (value: string) => {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat('en-GB', { day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit', hour12: false, timeZone: 'UTC' }).format(date);
};
const price = (value?: number) => typeof value === 'number' ? value.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 }) : '—';

function CandleChart({ candles, alerts, focusedAlert, onAlertFocus, loading }: {
  candles: Candle[]; alerts: Alert[]; focusedAlert: Alert | null; onAlertFocus: (alert: Alert) => void; loading: boolean;
}) {
  const [range, setRange] = useState(54);
  const [offset, setOffset] = useState(0);
  const [selected, setSelected] = useState<Candle | null>(null);
  const [dragX, setDragX] = useState<number | null>(null);
  const width = 1000, height = 330, pad = { left: 20, right: 76, top: 18, bottom: 34 };
  const visible = useMemo(() => {
    const end = Math.max(0, candles.length - offset);
    return candles.slice(Math.max(0, end - range), end);
  }, [candles, offset, range]);
  const computed = useMemo(() => {
    if (!visible.length) return null;
    const low = Math.min(...visible.map(c => c.low));
    const high = Math.max(...visible.map(c => c.high));
    const slack = Math.max((high - low) * .12, .4);
    return { low: low - slack, high: high + slack };
  }, [visible]);
  const plotW = width - pad.left - pad.right, plotH = height - pad.top - pad.bottom;
  const y = (value: number) => computed ? pad.top + ((computed.high - value) / (computed.high - computed.low)) * plotH : 0;
  const step = visible.length ? plotW / visible.length : plotW;
  const candleX = (i: number) => pad.left + step * i + step / 2;
  const activeCandle = selected ?? (focusedAlert?.candle ?? visible[visible.length - 1] ?? null);
  useEffect(() => {
    if (!focusedAlert) return;
    setSelected(focusedAlert.candle);
    const index = candles.findIndex(candle => candle.time === focusedAlert.candle.time);
    if (index >= 0 && (index < candles.length - offset - range || index >= candles.length - offset)) {
      setOffset(Math.max(0, candles.length - index - Math.floor(range / 2)));
    }
  }, [focusedAlert, candles, offset, range]);
  const alertCandles = new Map(alerts.map(a => [a.candle.time, a]));
  const handleWheel = (event: WheelEvent<HTMLDivElement>) => {
    event.preventDefault();
    setRange(current => Math.max(12, Math.min(150, current + (event.deltaY > 0 ? 8 : -8))));
  };
  const handlePointerDown = (event: PointerEvent<HTMLDivElement>) => {
    if (event.button === 0) { event.currentTarget.setPointerCapture(event.pointerId); setDragX(event.clientX); }
  };
  const handlePointerMove = (event: PointerEvent<HTMLDivElement>) => {
    if (dragX === null || !visible.length) return;
    const movement = event.clientX - dragX;
    const pixelsPerCandle = event.currentTarget.clientWidth / visible.length;
    const delta = Math.round(movement / Math.max(pixelsPerCandle, 3));
    if (Math.abs(delta) >= 1) { setOffset(value => Math.max(0, Math.min(candles.length - 1, value + delta))); setDragX(event.clientX); }
  };
  const moveFocus = (direction: number) => {
    const index = candles.findIndex(c => c.time === activeCandle?.time);
    const next = candles[Math.max(0, Math.min(candles.length - 1, (index < 0 ? candles.length - 1 : index) + direction))];
    if (next) setSelected(next);
  };
  const alertAt = (candle: Candle) => alertCandles.get(candle.time);
  return <section className="panel" aria-label="XAUUSD candle chart">
    <div className="panel-head">
      <div><div className="panel-title">Price action <span className="panel-kicker" style={{ marginLeft: 8 }}>XAUUSD · {focusedAlert?.time ? focusedAlert.time.slice(0, 10) : 'UTC'}</span></div></div>
      <div className="panel-kicker">OHLC · UTC</div>
    </div>
    {loading ? <div className="loading-skeleton" aria-label="Loading candles" /> : visible.length === 0 ?
      <div className="chart-empty"><div><strong>No approved candles in this window</strong>Choose another 2025 date range and load the bounded candle set.</div></div> :
      <>
        <div className="chart-meta">
          <div className="price-pair"><span className="price-big">{price(activeCandle?.close)}</span><span className={`price-change ${activeCandle && activeCandle.close >= activeCandle.open ? 'up' : 'down'}`}>{activeCandle ? (activeCandle.close >= activeCandle.open ? '▲' : '▼') : ''} close</span></div>
          <span className="panel-kicker">Approved candle tape</span>
          <span className="chart-window-label">{visible[0] ? formatDateTime(visible[0].time) : ''} — {visible.at(-1) ? formatDateTime(visible.at(-1)!.time) : ''}</span>
        </div>
        <div className="chart-wrap" onWheel={handleWheel} onPointerDown={handlePointerDown} onPointerMove={handlePointerMove} onPointerUp={() => setDragX(null)} onPointerCancel={() => setDragX(null)} data-testid="chart-interaction-area">
          <svg viewBox={`0 0 ${width} ${height}`} className="chart-svg" role="img" aria-label="Candlestick chart. Scroll to zoom and drag to navigate.">
            {[0, 1, 2, 3, 4].map(i => {
              const py = pad.top + plotH * i / 4;
              const value = computed ? computed.high - (computed.high - computed.low) * i / 4 : 0;
              return <g key={i}><line className="gridline" x1={pad.left} x2={width - pad.right} y1={py} y2={py} /><text x={width - pad.right + 9} y={py + 3}>{price(value)}</text></g>;
            })}
            {visible.map((candle, i) => {
              const x = candleX(i), rising = candle.close >= candle.open, color = rising ? '#56846e' : '#bd6959';
              const bodyTop = Math.min(y(candle.open), y(candle.close)), bodyHeight = Math.max(1.5, Math.abs(y(candle.open) - y(candle.close)));
              const alert = alertAt(candle);
              return <g key={`${candle.time}-${i}`} onMouseEnter={() => setSelected(candle)} onClick={() => alert && onAlertFocus(alert)} style={{ cursor: alert ? 'pointer' : 'crosshair' }}>
                <line x1={x} x2={x} y1={y(candle.high)} y2={y(candle.low)} stroke={color} strokeWidth="1.25" />
                <rect x={x - Math.max(2, step * .28)} y={bodyTop} width={Math.max(3, step * .56)} height={bodyHeight} fill={color} opacity={alert ? 1 : .82} />
                {alert && <path d={`M ${x} ${Math.max(8, y(candle.low) + 9)} l -4 5 h 8 z`} fill={alert.direction === 'BUY' ? '#c39e43' : '#bd6959'} />}
              </g>;
            })}
            {visible.filter((_, i) => i % Math.max(1, Math.ceil(visible.length / 6)) === 0).map(c => <text key={c.time} x={candleX(visible.indexOf(c))} y={height - 9} textAnchor="middle">{formatDateTime(c.time).split(',')[0]}</text>)}
            {activeCandle && <line x1={pad.left} x2={width - pad.right} y1={y(activeCandle.close)} y2={y(activeCandle.close)} stroke="#b49a58" strokeDasharray="3 4" opacity=".8" />}
          </svg>
        </div>
        <div className="chart-toolbar">
          <span>Drag to navigate · scroll to zoom · {visible.length} candles in view</span>
          <div className="chart-controls">
            <button className="icon-button" aria-label="Previous candle" data-testid="button-previous-candle" onClick={() => moveFocus(-1)}><ChevronLeft size={14}/></button>
            <button className="icon-button" aria-label="Zoom out" data-testid="button-zoom-out" onClick={() => setRange(v => Math.min(150, v + 12))}><Minus size={13}/></button>
            <button className="icon-button" aria-label="Zoom in" data-testid="button-zoom-in" onClick={() => setRange(v => Math.max(12, v - 12))}><Plus size={13}/></button>
            <button className="icon-button" aria-label="Next candle" data-testid="button-next-candle" onClick={() => moveFocus(1)}><ChevronRight size={14}/></button>
            <button className="icon-button" aria-label="Reset chart view" data-testid="button-reset-chart" onClick={() => { setOffset(0); setRange(54); setSelected(null); }}><RotateCcw size={12}/></button>
          </div>
        </div>
        <div className="data-row" data-testid="candle-ohlc">
          {(['open', 'high', 'low', 'close'] as const).map(key => <div className="data-cell" key={key}><span className="data-label">{key}</span><span className={`data-value mono ${key === 'close' && activeCandle && activeCandle.close >= activeCandle.open ? 'up' : key === 'close' ? 'down' : ''}`}>{price(activeCandle?.[key])}</span></div>)}
        </div>
      </>}
  </section>;
}

function Home() {
  const [timeframe, setTimeframe] = useState<Timeframe>('M5');
  const [from, setFrom] = useState('2025-01-01');
  const [to, setTo] = useState('2025-01-31');
  const [runResult, setRunResult] = useState<BacktestResult | null>(null);
  const [focusedAlert, setFocusedAlert] = useState<Alert | null>(null);
  const [directionFilter, setDirectionFilter] = useState('ALL');
  const [strengthFilter, setStrengthFilter] = useState('ALL');
  const [commentResult, setCommentResult] = useState<string | null>(null);
  const [commentRequested, setCommentRequested] = useState(false);
  const backtestResult = runResult && runResult.timeframe === timeframe && runResult.from === from && runResult.to === to ? runResult : null;
  const candles = backtestResult?.candles ?? fixture;
  const alerts = backtestResult?.alerts ?? [];
  const visibleAlerts = alerts.filter(alert => (directionFilter === 'ALL' || alert.direction === directionFilter) && (strengthFilter === 'ALL' || alert.strengthLabel === strengthFilter));
  const timeframeOptions = dataset.availableTimeframes;
  const dateInvalid = !from || !to || from > to || from < '2025-01-01' || to > '2025-12-31';
  const run = () => {
    if (dateInvalid) return;
    setFocusedAlert(null);
    setCommentResult(null);
    setCommentRequested(false);
    setRunResult({
      timeframe, from, to, candles: fixture, alerts: [],
      counts: { buy: 0, sell: 0, strong: 0, neutral: 0, weak: 0 },
      truncated: false, totalCandles: fixture.length, runId: 'LOCAL FIXTURE', durationMs: 0, specVersion: 'PREVIEW',
    });
  };
  const requestComment = () => {
    if (!focusedAlert) return;
    setCommentRequested(true);
    setCommentResult('Local preview only. No commentary service is connected.');
  };
  const focusAlert = (alert: Alert) => { setFocusedAlert(alert); setCommentResult(null); setCommentRequested(false); };
  const sourceName = dataset.source;
  return <div className="lab-shell">
    <header className="topbar">
      <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
        <div className="brand-mark"><span>F</span></div>
        <div><div style={{ fontSize: 12, fontWeight: 800, letterSpacing: '.06em' }}>FOREX STRATEGY LAB</div><div className="mono" style={{ fontSize: 9, color: '#b1b5b2', marginTop: 2 }}>RESEARCH CONTROL ROOM / 01</div></div>
      </div>
      <div className="mono" style={{ display: 'flex', gap: 8, alignItems: 'center', fontSize: 10, color: '#d2d5d0' }}><Activity size={13} color="#c8b36e"/> REVIEW MODE <span style={{ color: '#7c8686' }}>/</span> XAUUSD</div>
    </header>
    <main className="layout">
      <div className="page-head">
        <div><div className="eyebrow">Approved market data · 2025 research window</div><h1 className="page-title">Gold candle review</h1><div className="page-subtitle">Inspect source-verified candles and engine-generated informational alerts.</div></div>
        <div className="live-pill" data-testid="status-dataset"><span className="live-dot"/>VERIFIED SOURCE</div>
      </div>
      <div className="workspace-grid">
        <div style={{ display: 'flex', flexDirection: 'column', gap: 15 }}>
          <section className="panel">
            <div className="panel-head"><div><div className="panel-title">Review window</div><div className="panel-kicker" style={{ marginTop: 4 }}>Bounded candle query · no generated price data</div></div><Crosshair size={16} color="#a89051"/></div>
            <div className="controls">
              <div><label className="field-label" htmlFor="date-from">From · UTC</label><input id="date-from" className="control-input" type="date" min="2025-01-01" max="2025-12-31" value={from} onChange={e => setFrom(e.target.value)} data-testid="input-from-date"/></div>
              <div><label className="field-label" htmlFor="date-to">To · UTC</label><input id="date-to" className="control-input" type="date" min="2025-01-01" max="2025-12-31" value={to} onChange={e => setTo(e.target.value)} data-testid="input-to-date"/></div>
              <div><span className="field-label">Timeframe</span><div className="tf-row">
                {(['M5', 'M15', 'M1'] as Timeframe[]).map(tf => <button key={tf} className={`tf-btn ${timeframe === tf ? 'selected' : ''}`} disabled={!timeframeOptions.includes(tf)} onClick={() => setTimeframe(tf)} data-testid={`button-timeframe-${tf.toLowerCase()}`}>{tf}</button>)}
              </div></div>
              <button className="run-btn" onClick={run} disabled={dateInvalid} data-testid="button-run-smoke"><Activity size={14}/>{'RUN UNSCORED SMOKE'}</button>
              {timeframe === 'M1' && <div className="m1-note" data-testid="notice-m1-review">M1 fires on ~1 in 4 candles — for engine checks only, not human review.</div>}
              {dateInvalid && <div className="m1-note" style={{ gridColumn: '1/-1', color: '#9a573e' }} role="alert" data-testid="error-date-range">Choose a valid date window within 2025.</div>}
            </div>
          </section>
          <CandleChart candles={candles} alerts={alerts} focusedAlert={focusedAlert} onAlertFocus={focusAlert} loading={false}/>
          {backtestResult && <div className="panel status-note" data-testid="status-run">LOCAL FIXTURE · No engine smoke run or score was produced; fixed candles and zero alerts only.</div>}
        </div>
        <aside className="side-stack">
          <section className="panel">
            <div className="panel-head"><span className="panel-title">Dataset provenance</span><Database size={15} color="#a89051"/></div>
            <div className="dataset-card">
              <div className="live-pill" style={{ display: 'inline-flex' }}><span className="live-dot"/> APPROVED LOCAL FILE</div>
              <p className="source-line">{sourceName}<br/>XAUUSD historical candle source</p>
              <div className="dataset-stats">
                <div><span className="stat-label">Coverage</span><span className="stat-value">{dataset.startDate.slice(0, 10)} — {dataset.endDate.slice(0, 10)}</span></div>
                <div><span className="stat-label">Candles</span><span className="stat-value">{dataset.candleCount.toLocaleString()}</span></div>
                <div><span className="stat-label">Timeframes</span><span className="stat-value">{dataset.availableTimeframes.join(' · ')}</span></div>
                <div><span className="stat-label">SHA-256</span><span className="stat-value hash">{dataset.sourceSha256}</span></div>
              </div>
            </div>
          </section>
          <section className="panel alerts-panel">
            <div className="panel-head"><div><div className="panel-title">Engine alerts</div><div className="panel-kicker" style={{ marginTop: 4 }}>{backtestResult ? `${alerts.length} informational · unscored` : 'Run a smoke window to inspect alerts'}</div></div><span className="mono" style={{ color: '#89877e', fontSize: 10 }}>{visibleAlerts.length} / {alerts.length}</span></div>
            {backtestResult && <div className="result-strip">
              <div className="result-stat"><span className="result-num">{backtestResult.counts.buy}</span><span className="result-caption">Buy</span></div>
              <div className="result-stat"><span className="result-num">{backtestResult.counts.sell}</span><span className="result-caption">Sell</span></div>
              <div className="result-stat"><span className="result-num">{backtestResult.counts.strong}</span><span className="result-caption">Strong</span></div>
              <div className="result-stat"><span className="result-num">{backtestResult.counts.neutral}</span><span className="result-caption">Neutral</span></div>
              <div className="result-stat"><span className="result-num">{backtestResult.counts.weak}</span><span className="result-caption">Weak</span></div>
            </div>}
            {backtestResult && <>
              <div className="alert-filters">
                <select className="filter-select" value={directionFilter} onChange={e => setDirectionFilter(e.target.value)} aria-label="Filter alerts by direction" data-testid="filter-alert-direction"><option value="ALL">BUY / SELL: all</option><option value="BUY">BUY</option><option value="SELL">SELL</option></select>
                <select className="filter-select" value={strengthFilter} onChange={e => setStrengthFilter(e.target.value)} aria-label="Filter alerts by strength" data-testid="filter-alert-strength"><option value="ALL">Strength: all</option><option value="STRONG">STRONG</option><option value="NEUTRAL">NEUTRAL</option><option value="WEAK">WEAK</option></select>
              </div>
              <div className="alerts-list">
                {visibleAlerts.length === 0 ? <div className="chart-empty" style={{ minHeight: 145 }} data-testid="empty-alerts"><div><strong>No alerts match these filters</strong>Adjust the alert filters to inspect this run.</div></div> : visibleAlerts.map(alert => <div key={alert.alertId} role="button" tabIndex={0} className={`alert-row ${focusedAlert?.alertId === alert.alertId ? 'active' : ''}`} onClick={() => focusAlert(alert)} onKeyDown={e => { if (e.key === 'Enter' || e.key === ' ') focusAlert(alert); }} data-testid={`alert-row-${alert.alertId}`}>
                  <div className="alert-left"><span className={`dir-mark ${alert.direction === 'SELL' ? 'sell' : ''}`}>{alert.direction === 'BUY' ? <ArrowUpRight size={15}/> : <ArrowDownRight size={15}/>}</span><div><div className="alert-time">{formatDateTime(alert.time)}</div><div className="alert-tags">CCT {alert.cct} · CST {alert.cst} · {alert.timeframe}</div></div></div>
                  <span className={`strength ${alert.strengthLabel}`}>{alert.strengthLabel}</span>
                </div>)}
              </div>
            </>}
            {!backtestResult && <div className="status-note">Choose a 2025 window and run the existing smoke path. No score or performance outcome is produced.</div>}
          </section>
          <section className="panel ai-box">
            <div className="ai-header"><span className="panel-title">AI journal note</span><span className="panel-kicker">OPTIONAL</span></div>
            <div className="ai-copy">{focusedAlert ? `Generate a contextual journal note for ${focusedAlert.direction} · ${formatDateTime(focusedAlert.time)}. Comments are requested only when you ask.` : 'Select an alert to request an objective journal comment. The fixed commentary guardrail is enforced server-side.'}</div>
            {focusedAlert && <button className="ai-btn" style={{ marginTop: 12 }} onClick={requestComment} disabled={commentRequested} data-testid="button-generate-ai-comment">{commentRequested ? 'GENERATED' : 'GENERATE COMMENT'}</button>}
            {commentResult && <div className="ai-result" data-testid="result-ai-comment">{commentResult}</div>}
          </section>
        </aside>
      </div>
    </main>
    <div className="disclaimer" data-testid="persistent-disclaimer"><strong>RESEARCH ONLY</strong> Informational only — not a trading recommendation. No profitability claims.</div>
  </div>;
}

export function Current() {
  return <Home />;
}
