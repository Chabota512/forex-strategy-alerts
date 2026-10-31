import './_group.css';
import { useEffect, useMemo, useState } from 'react';
import { ArrowDownRight, ArrowUpRight, ChevronLeft, ChevronRight, Pause, Play, RotateCcw, SkipBack } from 'lucide-react';

type Candle = { time: string; open: number; high: number; low: number; close: number };
type TapeAlert = { index: number; side: 'BUY' | 'SELL'; grade: 'STRONG' | 'NEUTRAL' | 'WEAK' };

// Exact approved XAUUSD M5 fixture extracted from Current.tsx.
const fixture: Candle[] = [
  ['2025-01-31T16:40:00Z',2807.765,2809.455,2806.385,2807.095],['2025-01-31T16:45:00Z',2807.095,2807.425,2805.825,2806.385],
  ['2025-01-31T16:50:00Z',2806.265,2808.325,2805.665,2808.165],['2025-01-31T16:55:00Z',2808.235,2808.315,2805.695,2807.295],
  ['2025-01-31T17:00:00Z',2807.315,2809.395,2807.095,2808.925],['2025-01-31T17:05:00Z',2808.965,2809.815,2805.305,2806.355],
  ['2025-01-31T17:10:00Z',2806.355,2807.295,2802.895,2806.295],['2025-01-31T17:15:00Z',2806.128,2812.655,2803.405,2810.835],
  ['2025-01-31T17:20:00Z',2810.875,2811.948,2807.975,2808.848],['2025-01-31T17:25:00Z',2808.795,2809.248,2805.915,2807.715],
  ['2025-01-31T17:30:00Z',2807.715,2809.885,2806.965,2808.778],['2025-01-31T17:35:00Z',2808.698,2812.495,2808.118,2812.305],
  ['2025-01-31T17:40:00Z',2812.295,2816.088,2812.225,2813.295],['2025-01-31T17:45:00Z',2813.598,2815.978,2811.015,2812.665],
  ['2025-01-31T17:50:00Z',2812.665,2813.065,2809.185,2810.178],['2025-01-31T17:55:00Z',2810.048,2810.435,2807.865,2808.475],
  ['2025-01-31T18:00:00Z',2808.468,2808.875,2806.215,2806.298],['2025-01-31T18:05:00Z',2806.578,2808.248,2805.538,2806.915],
  ['2025-01-31T18:10:00Z',2806.925,2807.675,2805.015,2805.165],['2025-01-31T18:15:00Z',2805.058,2805.448,2801.148,2801.675],
  ['2025-01-31T18:20:00Z',2801.675,2804.448,2798.775,2802.575],['2025-01-31T18:25:00Z',2802.568,2802.618,2798.215,2799.565],
  ['2025-01-31T18:30:00Z',2799.565,2799.655,2795.075,2796.315],['2025-01-31T18:35:00Z',2796.285,2801.515,2796.095,2800.995],
  ['2025-01-31T18:40:00Z',2801.005,2803.065,2799.925,2802.095],['2025-01-31T18:45:00Z',2802.105,2802.295,2799.265,2800.335],
  ['2025-01-31T18:50:00Z',2800.415,2800.765,2797.445,2798.595],['2025-01-31T18:55:00Z',2798.595,2799.385,2797.775,2798.685],
  ['2025-01-31T19:00:00Z',2798.725,2798.945,2797.425,2797.885],['2025-01-31T19:05:00Z',2797.915,2798.785,2796.735,2797.995],
  ['2025-01-31T19:10:00Z',2798.085,2800.925,2797.295,2800.775],['2025-01-31T19:15:00Z',2800.765,2800.885,2798.765,2799.555],
  ['2025-01-31T19:20:00Z',2799.535,2801.415,2799.465,2801.215],['2025-01-31T19:25:00Z',2801.235,2801.675,2800.325,2801.605],
  ['2025-01-31T19:30:00Z',2801.585,2802.595,2799.795,2800.435],['2025-01-31T19:35:00Z',2800.458,2800.825,2799.215,2800.115],
  ['2025-01-31T19:40:00Z',2800.055,2800.185,2795.898,2796.428],['2025-01-31T19:45:00Z',2796.225,2797.568,2795.118,2797.425],
  ['2025-01-31T19:50:00Z',2797.425,2797.965,2796.165,2797.215],['2025-01-31T19:55:00Z',2797.175,2797.495,2794.818,2795.905],
  ['2025-01-31T20:00:00Z',2795.955,2796.108,2793.965,2794.565],['2025-01-31T20:05:00Z',2794.545,2796.375,2794.305,2796.245],
  ['2025-01-31T20:10:00Z',2796.285,2797.395,2795.975,2797.065],['2025-01-31T20:15:00Z',2797.065,2797.365,2795.135,2796.455],
  ['2025-01-31T20:20:00Z',2796.435,2796.985,2795.815,2796.705],['2025-01-31T20:25:00Z',2796.725,2797.055,2795.865,2796.835],
  ['2025-01-31T20:30:00Z',2796.865,2797.325,2795.898,2796.385],['2025-01-31T20:35:00Z',2796.265,2797.475,2796.185,2797.285],
  ['2025-01-31T20:40:00Z',2797.265,2798.685,2796.378,2798.655],['2025-01-31T20:45:00Z',2798.635,2800.175,2798.415,2799.975],
  ['2025-01-31T20:50:00Z',2799.975,2800.938,2798.898,2799.855],['2025-01-31T20:55:00Z',2799.848,2801.988,2798.958,2800.698],
  ['2025-01-31T21:00:00Z',2800.738,2801.555,2798.808,2801.505],['2025-01-31T21:05:00Z',2801.495,2802.035,2800.125,2800.998],
  ['2025-01-31T21:10:00Z',2800.998,2801.558,2799.498,2801.165],['2025-01-31T21:15:00Z',2801.518,2802.295,2801.008,2801.428],
  ['2025-01-31T21:20:00Z',2801.508,2801.958,2800.498,2801.135],['2025-01-31T21:25:00Z',2801.308,2801.308,2799.888,2800.858],
  ['2025-01-31T21:30:00Z',2800.928,2801.178,2800.128,2800.868],['2025-01-31T21:35:00Z',2800.708,2801.038,2798.495,2798.825],
  ['2025-01-31T21:40:00Z',2798.825,2799.505,2798.135,2799.465],['2025-01-31T21:45:00Z',2799.405,2800.785,2799.405,2800.705],
  ['2025-01-31T21:50:00Z',2800.735,2801.925,2799.545,2799.825],['2025-01-31T21:55:00Z',2800.075,2800.845,2796.605,2798.085],
].map(([time, open, high, low, close]) => ({ time: time as string, open: open as number, high: high as number, low: low as number, close: close as number }));

// Illustrative local tape entries only; no strategy rules or signal calculations run here.
const tape: TapeAlert[] = [
  { index: 2, side: 'BUY', grade: 'NEUTRAL' },
  { index: 5, side: 'SELL', grade: 'NEUTRAL' },
  { index: 17, side: 'BUY', grade: 'NEUTRAL' },
  { index: 20, side: 'BUY', grade: 'WEAK' },
  { index: 29, side: 'BUY', grade: 'WEAK' },
  { index: 34, side: 'SELL', grade: 'STRONG' },
  { index: 37, side: 'BUY', grade: 'WEAK' },
  { index: 38, side: 'SELL', grade: 'STRONG' },
  { index: 46, side: 'SELL', grade: 'STRONG' },
  { index: 50, side: 'SELL', grade: 'STRONG' },
  { index: 53, side: 'SELL', grade: 'STRONG' },
  { index: 54, side: 'BUY', grade: 'WEAK' },
  { index: 55, side: 'SELL', grade: 'STRONG' },
  { index: 60, side: 'BUY', grade: 'WEAK' },
  { index: 62, side: 'SELL', grade: 'STRONG' },
];
const price = (n: number) => n.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 3 });
const time = (s: string) => new Intl.DateTimeFormat('en-GB', { day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit', hour12: false, timeZone: 'UTC' }).format(new Date(s));

export function ReplayFirst() {
  const [cursor, setCursor] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [speed, setSpeed] = useState(1);
  const [selected, setSelected] = useState<number | null>(null);
  const revealed = fixture.slice(0, cursor + 1);
  const candle = fixture[cursor];
  const visibleTape = tape.filter(item => item.index <= cursor);
  const range = useMemo(() => {
    const lo = Math.min(...revealed.map(c => c.low));
    const hi = Math.max(...revealed.map(c => c.high));
    const margin = Math.max((hi - lo) * .11, .25);
    return { low: lo - margin, high: hi + margin };
  }, [revealed]);
  useEffect(() => {
    if (!playing) return;
    const id = window.setInterval(() => setCursor(i => Math.min(fixture.length - 1, i + 1)), 800 / speed);
    return () => window.clearInterval(id);
  }, [playing, speed]);
  useEffect(() => { if (playing && cursor >= fixture.length - 1) setPlaying(false); }, [playing, cursor]);
  const seek = (index: number) => { setPlaying(false); setCursor(index); };
  const graphW = 900, graphH = 350, left = 8, right = 72, top = 14, bottom = 30;
  const plotW = graphW - left - right, plotH = graphH - top - bottom;
  const y = (n: number) => top + (range.high - n) / (range.high - range.low) * plotH;
  const spacing = plotW / revealed.length;

  return <div className="replay-mock">
    <style>{`
      .replay-mock{min-height:100vh;background:#171c22;color:#c7cbd0;font:10px 'DM Mono',monospace;padding:0 13px 42px;box-sizing:border-box}
      .replay-mock *{box-sizing:border-box}.rp-top{height:43px;margin:0 -13px;padding:0 15px;display:flex;justify-content:space-between;align-items:center;background:#191e24;border-bottom:1px solid #303740}
      .rp-symbol{font-size:13px;color:#e0e2e4}.rp-sub{color:#7e8791;font-size:9px;margin-left:10px}.rp-meta{color:#7a838c;font-size:9px}
      .rp-config,.rp-control{min-height:46px;display:flex;align-items:center;gap:9px;border-bottom:1px solid #303740;flex-wrap:wrap}
      .rp-config input{height:26px;width:126px;background:#1a2026;color:#bbc0c5;border:1px solid #353c45;padding:0 6px;font:9px 'DM Mono',monospace}
      .rp-token{height:25px;padding:0 9px;background:#343126;color:#e1c985;border:1px solid #63583b;font:9px 'DM Mono',monospace}
      .rp-control{gap:6px}.rp-control button{width:27px;height:26px;display:grid;place-items:center;background:#1d232a;border:1px solid #353c44;color:#acb2b8;cursor:pointer}
      .rp-control button:disabled{opacity:.3;cursor:default}.rp-control .rp-play{color:#d4bc77;background:#28261f}.rp-slider{flex:1;min-width:90px;margin:0 7px;accent-color:#b89e5e}
      .rp-rate{display:flex}.rp-rate button{width:auto;padding:0 7px;font:8px 'DM Mono',monospace}.rp-rate .on{color:#e0c780;background:#343126}
      .rp-time{min-width:154px;text-align:right;color:#abb1b6;font-size:9px}.rp-progress{color:#737c85;margin-left:7px}
      .rp-grid{display:grid;grid-template-columns:minmax(0,1fr) 260px;gap:10px;padding-top:10px}
      .rp-chart,.rp-feed{min-width:0;background:#171c21;border:1px solid #303740}
      .rp-heading{height:38px;padding:0 10px;display:flex;justify-content:space-between;align-items:center;border-bottom:1px solid #2c333a;color:#dedfe1}
      .rp-heading span{color:#77818a;font-size:8px}.rp-oh{display:flex;gap:10px;color:#adb3b8;font-size:9px}
      .rp-svg{display:block;width:100%;height:auto}.rp-svg text{font:9px 'DM Mono',monospace;fill:#737c85}
      .rp-svg .grid{stroke:#2a3037;stroke-width:1}.rp-line{stroke:#a89256;stroke-dasharray:4 4}
      .rp-marker{cursor:pointer}.rp-marker circle{stroke:#171c21;stroke-width:1.2}.rp-marker text{fill:#f3f0e8;font-size:8px}
      .rp-foot{height:28px;padding:0 9px;display:flex;justify-content:space-between;align-items:center;border-top:1px solid #2d343c;color:#69737c;font-size:8px}
      .rp-feed-title{height:38px;padding:0 10px;display:flex;align-items:center;justify-content:space-between;border-bottom:1px solid #2d343c}.rp-feed-title strong{font-weight:500;letter-spacing:.06em}.rp-feed-title b{color:#c6aa62;font-size:13px;font-weight:400}
      .rp-counts{height:28px;display:flex;justify-content:space-around;align-items:center;border-bottom:1px solid #2d343c;color:#717b84;font-size:8px}.rp-counts b{color:#b8bec2;font-weight:400}
      .rp-alert{min-height:54px;display:flex;gap:8px;align-items:center;padding:7px;border-bottom:1px solid #292f36;cursor:pointer}.rp-alert:hover,.rp-alert.active{background:#22282e}
      .rp-dir{width:23px;height:23px;display:grid;place-items:center;background:#1d302b;color:#73b494}.rp-dir.sell{background:#332423;color:#d47a72}
      .rp-text{min-width:0;flex:1;display:flex;flex-direction:column;gap:4px}.rp-text b{display:flex;justify-content:space-between;color:#c3c8cc;font-weight:500}.rp-text small{color:#a08b54;font-size:8px}.rp-text i{font-style:normal;color:#89919a;font-size:8px}.rp-text em{font-style:normal;color:#68737c;font-size:8px}
      .rp-disclaimer{position:fixed;bottom:0;left:0;right:0;min-height:31px;display:flex;justify-content:center;align-items:center;background:#171b20;border-top:1px solid #353c44;color:#858c92;font-size:8px}
      @media(max-width:760px){.rp-grid{grid-template-columns:1fr}.rp-feed{max-height:270px;overflow:auto}.rp-time{min-width:0;width:100%;text-align:left}.rp-control{padding:5px 0}.rp-config{gap:6px}.rp-meta{display:none}.rp-heading{gap:5px}.rp-oh{gap:5px;font-size:8px}}
    `}</style>
    <header className="rp-top"><div><b className="rp-symbol">XAUUSD</b><span className="rp-sub">GOLD / U.S. DOLLAR</span></div><div className="rp-meta">APPROVED M5 FIXTURE · 2025-01-31</div></header>
    <section className="rp-config">
      <label>FROM <input type="date" value="2025-01-31" readOnly/></label><label>TO <input type="date" value="2025-01-31" readOnly/></label>
      <span className="rp-token">M5</span><span className="rp-meta">APPROVED XAUUSD M5 FIXTURE · 64 CANDLES</span>
    </section>
    <section className="rp-control">
      <button onClick={() => { setPlaying(false); setCursor(0); }} aria-label="Reset"><SkipBack size={13}/></button>
      <button disabled={cursor === 0} onClick={() => seek(cursor - 1)} aria-label="Previous candle"><ChevronLeft size={15}/></button>
      <button className="rp-play" onClick={() => { if (cursor === fixture.length - 1) setCursor(0); setPlaying(v => !v); }} aria-label={playing ? 'Pause' : 'Play'}>{playing ? <Pause size={13}/> : <Play size={13}/>}</button>
      <button disabled={cursor === fixture.length - 1} onClick={() => seek(cursor + 1)} aria-label="Next candle"><ChevronRight size={15}/></button>
      <button onClick={() => { setPlaying(false); setCursor(0); }} aria-label="Reset replay"><RotateCcw size={12}/></button>
      <input className="rp-slider" type="range" min="0" max={fixture.length - 1} value={cursor} onChange={e => seek(Number(e.target.value))} aria-label="Replay position"/>
      <div className="rp-rate">{[0.5,1,2,5].map(rate => <button key={rate} className={speed === rate ? 'on' : ''} onClick={() => setSpeed(rate)}>{rate}×</button>)}</div>
      <div className="rp-time">{time(candle.time)}<span className="rp-progress">{cursor + 1} / {fixture.length}</span></div>
    </section>
    <div className="rp-grid">
      <section className="rp-chart">
        <div className="rp-heading">M5 <span>· XAUUSD · HISTORICAL</span><div className="rp-oh"><span>O {price(candle.open)}</span><span>H {price(candle.high)}</span><span>L {price(candle.low)}</span><span>C {price(candle.close)}</span></div></div>
        <svg className="rp-svg" viewBox={`0 0 ${graphW} ${graphH}`} role="img" aria-label="Candlestick historical replay">
          {[0,1,2,3,4].map(i=>{const py=top+plotH*i/4,v=range.high-(range.high-range.low)*i/4;return <g key={i}><line className="grid" x1={left} x2={graphW-right} y1={py} y2={py}/><text x={graphW-right+8} y={py+3}>{price(v)}</text></g>;})}
          {revealed.map((c,i)=>{const x=left+spacing*(i+.5),up=c.close>=c.open,col=up?'#58a887':'#d46d67',alert=tape.find(a=>a.index===i),by=Math.min(y(c.open),y(c.close)),bh=Math.max(1.4,Math.abs(y(c.open)-y(c.close)));return <g key={c.time} onClick={()=>alert&&setSelected(i)}>
            <line x1={x} x2={x} y1={y(c.high)} y2={y(c.low)} stroke={col} strokeWidth="1.15"/><rect x={x-Math.max(2,spacing*.3)} y={by} width={Math.max(2.5,spacing*.6)} height={bh} fill={col}/>
            {alert&&<g className="rp-marker" onClick={()=>{setPlaying(false);setSelected(i);}}><circle cx={x} cy={Math.max(top+10,y(c.low)+13)} r="8" fill={alert.side==='BUY'?'#428b70':'#bb625d'}/><text x={x} y={Math.max(top+13,y(c.low)+16)} textAnchor="middle">{alert.side==='BUY'?'B':'S'}</text></g>}
          </g>;})}
          <line className="rp-line" x1={left} x2={graphW-right} y1={y(candle.close)} y2={y(candle.close)}/>
          <text x={left} y={graphH-8}>{time(revealed[0].time)}</text><text x={graphW-right-60} y={graphH-8}>{time(candle.time)}</text>
        </svg>
        <div className="rp-foot"><span>APPROVED HISTORICAL CANDLES · UTC</span><span>{revealed.length} REVEALED</span></div>
      </section>
      <aside className="rp-feed">
        <div className="rp-feed-title"><strong>ALERT TAPE <small style={{color:'#68727b',fontSize:8,fontWeight:400}}>· LOCAL PREVIEW</small></strong><b>{visibleTape.length.toString().padStart(2,'0')}</b></div>
        <div className="rp-counts"><span>BUY <b>{visibleTape.filter(a=>a.side==='BUY').length}</b></span><span>SELL <b>{visibleTape.filter(a=>a.side==='SELL').length}</b></span><span>STR <b>{visibleTape.filter(a=>a.grade==='STRONG').length}</b></span></div>
        {visibleTape.length ? [...visibleTape].reverse().map(a=><div key={a.index} role="button" tabIndex={0} className={`rp-alert ${selected===a.index?'active':''}`} onClick={()=>{setPlaying(false);setCursor(a.index);setSelected(a.index);}} onKeyDown={e=>{if(e.key==='Enter') {setPlaying(false);setCursor(a.index);setSelected(a.index);}}}>
          <span className={`rp-dir ${a.side==='SELL'?'sell':''}`}>{a.side==='BUY'?<ArrowUpRight size={14}/>:<ArrowDownRight size={14}/>}</span><span className="rp-text"><b>{a.side}<small>{a.grade}</small></b><i>{time(fixture[a.index].time)} UTC</i><em>XAUUSD · M5</em></span>
        </div>) : <div className="rp-meta" style={{padding:14}}>No alerts revealed at this candle.</div>}
      </aside>
    </div>
    <footer className="rp-disclaimer">Informational only — not a trading recommendation. No profitability claims.</footer>
  </div>;
}
