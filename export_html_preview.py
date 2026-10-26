"""
Tmap -> HTML preview exporter.

Runs the REAL pipeline (synthetic ticks -> RecordingSession -> DOMCache ->
frames -> TileStore tiles on disk), then reads it back through
store.query_window() and renders everything into a single self-contained
interactive HTML file (canvas 2D, pan/zoom/hover, no external deps).

Output: /workspace/tmap_preview.html
Open it in any browser — or just view it in the side canvas panel.
"""
from __future__ import annotations

import json
import math
import random
import time

from tmap.core.types import Tick, TradeSide
from tmap.core.heatmap_store import TileStore, RecordingSession

OUT = "/workspace/tmap_preview.html"
SYMBOL = "XAUUSD"
TICK_SIZE = 0.01
N_TICKS = 9000
VIRT_SECONDS = 60.0          # whole session compressed into 60 virtual seconds
TILE_S = 15.0               # small tiles so we get several flushed to disk
FRAME_S = 0.5


def gen_ticks(t0: float):
    """Random-walk gold prices with occasional liquidity 'whale' blocks."""
    rng = random.Random(42)
    price = 2750.0
    spread = 0.05
    trend = 0.0
    dt = VIRT_SECONDS / N_TICKS
    ts = t0
    for i in range(N_TICKS):
        if rng.random() < 0.004:            # regime shift
            trend = rng.uniform(-0.03, 0.03)
        step = rng.gauss(trend, 0.06)
        price = max(2700.0, min(2800.0, price + step))
        side = TradeSide.BUY if step >= 0 else TradeSide.SELL
        vol = rng.expovariate(1.0 / 1.5)
        if rng.random() < 0.01:             # whale trade
            vol *= rng.uniform(8, 25)
        bid = round(price - spread / 2, 2)
        ask = round(price + spread / 2, 2)
        yield Tick(timestamp=ts, price=round(price, 2), volume=round(vol, 2),
                   side=side, bid=bid, ask=ask)
        ts += dt


def main():
    t0 = float(int(time.time()) // 60 * 60)  # align to minute
    store = TileStore(root="data/html_tiles", tile_seconds=TILE_S,
                      frame_interval_s=FRAME_S)
    sess: RecordingSession = store.open_session(SYMBOL, tick_size=TICK_SIZE)
    for tk in gen_ticks(t0):
        sess.ingest(tk)
    metas = sess.finish()

    t_end = t0 + VIRT_SECONDS

    # ---- pick a price band that actually contains the DOM liquidity ------
    # XAUUSD tick grid is $0.01 and DOMCache tracks +-200 levels (~$2) around
    # mid, so the full traded range would render as sub-pixel noise. Export a
    # window centred on the session's median mid covering the live depth band.
    probe = store.query_window(SYMBOL, t0 - 1, t_end + 1, 2600.0, 2900.0)
    assert probe, "query_window returned nothing"
    mids = sorted(f.mid_price for f in probe)
    med = mids[len(mids) // 2]
    lo_p, hi_p = med - 1.35, med + 1.35      # ~$2.7 band => crisp rows in browser
    frames = store.query_window(SYMBOL, t0 - 1, t_end + 1, lo_p, hi_p)
    assert frames, "band query returned nothing"

    # ---- serialize frames -> compact JSON -------------------------------
    all_idx = set()
    for f in frames:
        all_idx.update(f.cells.keys())
        all_idx.update(p for p, _, _ in f.trades)
    idxs = sorted(all_idx)
    pos_of = {pi: k for k, pi in enumerate(idxs)}
    prices = [round(pi * TICK_SIZE, 2) for pi in idxs]

    out_frames = []
    for f in frames:
        cells = []
        for pi, c in sorted(f.cells.items()):
            cells.append([pos_of[pi], round(c.bid, 2), round(c.ask, 2)])
        trades = [[pos_of[pi], round(v, 2), s] for pi, v, s in f.trades]
        out_frames.append({
            "t": round(f.timestamp - t0, 3),
            "mid": round(f.mid_price, 2),
            "c": cells,
            "tr": trades,
        })

    data = {
        "symbol": SYMBOL,
        "tickSize": TICK_SIZE,
        "duration": VIRT_SECONDS,
        "prices": prices,
        "frames": out_frames,
        "tiles": [{"start": m.start_ts - t0, "end": m.end_ts - t0,
                   "frames": m.n_frames, "bytes": m.size_bytes} for m in metas],
    }
    payload = json.dumps(data, separators=(",", ":"))

    html = HTML_TMPL.replace("__DATA__", payload)
    with open(OUT, "w", encoding="utf-8") as fh:
        fh.write(html)

    print(f"tiles flushed : {len(metas)}")
    for m in metas:
        print(f"  {m.start_ts - t0:>6.1f}s .. {m.end_ts - t0:>6.1f}s  "
              f"{m.n_frames:>4} frames  {m.size_bytes/1024:7.1f} KB")
    print(f"frames queried: {len(frames)}  price levels: {len(prices)}")
    print(f"wrote {OUT} ({len(html)/1024:.0f} KB)")


HTML_TMPL = r"""<!DOCTYPE html>
<html lang="id">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Tmap — XAUUSD Liquidity Heatmap Preview</title>
<style>
  :root{--bg:#0b0e14;--panel:#11151f;--fg:#d8dee9;--mut:#5c6572;
        --grid:#1c2230;--acc:#f5c518;}
  *{box-sizing:border-box;margin:0;padding:0}
  html,body{height:100%}
  body{background:var(--bg);color:var(--fg);
       font:13px/1.45 ui-monospace,'SF Mono',Menlo,Consolas,monospace;
       display:flex;flex-direction:column}
  header{padding:10px 16px;background:var(--panel);border-bottom:1px solid var(--grid);
         display:flex;gap:14px;align-items:center;flex-wrap:wrap}
  header h1{font-size:15px;color:var(--acc);letter-spacing:.5px}
  .badge{font-size:11px;color:var(--mut);border:1px solid var(--grid);
         border-radius:4px;padding:2px 8px}
  .badge b{color:var(--fg)}
  #hint{margin-left:auto;color:var(--mut);font-size:11px}
  #wrap{flex:1;position:relative;min-height:0}
  canvas{position:absolute;inset:0;width:100%;height:100%;display:block;cursor:crosshair}
  #tip{position:absolute;pointer-events:none;background:#161b26ee;border:1px solid var(--grid);
       border-radius:6px;padding:6px 9px;font-size:11px;display:none;z-index:5;white-space:pre;
       box-shadow:0 4px 16px #000a}
  footer{padding:6px 16px;background:var(--panel);border-top:1px solid var(--grid);
         color:var(--mut);font-size:11px;display:flex;gap:18px;flex-wrap:wrap}
  footer b{color:var(--fg)}
</style>
</head>
<body>
<header>
  <h1>◈ Tmap</h1>
  <span class="badge"><b id="sym"></b> · synthetic L2 reconstruction</span>
  <span class="badge">tile store: <b id="ntiles"></b> tiles .tmht</span>
  <span class="badge">history: <b id="dur"></b>s @ <b id="fi"></b>0.5s frames</span>
  <span id="hint">drag = pan · wheel = zoom time · ctrl+wheel = zoom price · R = reset</span>
</header>
<div id="wrap">
  <canvas id="cv"></canvas>
  <div id="tip"></div>
</div>
<footer>
  <span>price axis: <b id="pband"></b></span>
  <span><span style="color:#2fbf71">■</span> resting bid &nbsp; <span style="color:#e0525f">■</span> resting ask (intensity = lot size)</span>
  <span><span style="color:#2fbf71">▲</span>/<span style="color:#e0525f">▼</span> aggressor trades</span>
  <span>yellow line = mid price</span>
</footer>
<script>
const D = __DATA__;
document.getElementById('sym').textContent = D.symbol;
document.getElementById('ntiles').textContent = D.tiles.length;
document.getElementById('dur').textContent = D.duration.toFixed(0);

const cv = document.getElementById('cv'), ctx = cv.getContext('2d');
const tip = document.getElementById('tip');
const PAD_L = 64, PAD_B = 26, PAD_T = 8, PAD_R = 10;

// view state (time window + price center + px-per-second / px-per-dollar)
let view = null;
function defaultView(){
  const pmin = Math.min(...D.prices), pmax = Math.max(...D.prices);
  return { t0:0, t1:D.duration, pc:(pmin+pmax)/2, pw:pmax-pmin+0.4,
           pps: (innerWidth-PAD_L-PAD_R)/D.duration };
}
view = defaultView();

let W=0,H=0,DPR=1;
function resize(){
  DPR = window.devicePixelRatio||1;
  W = cv.clientWidth; H = cv.clientHeight;
  cv.width = W*DPR; cv.height = H*DPR;
  ctx.setTransform(DPR,0,0,DPR,0,0);
  draw();
}

const xOf = t => PAD_L + (t - view.t0)*view.pps;
const tOf = x => view.t0 + (x - PAD_L)/view.pps;
const pmin = () => view.pc - view.pw/2, pmax = () => view.pc + view.pw/2;
const yOf = p => PAD_T + (pmax()-p)/(view.pw)*(H-PAD_T-PAD_B);
const pOf = y => pmax()-(y-PAD_T)/(H-PAD_T-PAD_B)*(view.pw);

// precompute max strength for color scaling
let MAXS = 1;
for(const f of D.frames) for(const c of f.c){ MAXS=Math.max(MAXS,c[1],c[2]); }

function heatColor(v, isBid){
  const a = Math.min(1, Math.sqrt(v/MAXS)*1.25);
  return isBid ? `rgba(47,191,113,${a})` : `rgba(224,82,95,${a})`;
}

function draw(){
  ctx.clearRect(0,0,W,H);
  ctx.fillStyle='#0b0e14'; ctx.fillRect(0,0,W,H);
  const plotW = W-PAD_L-PAD_R, plotH = H-PAD_T-PAD_B;

  // grid — time
  ctx.strokeStyle='#1c2230'; ctx.fillStyle='#5c6572';
  ctx.font='10px ui-monospace,monospace'; ctx.textAlign='center';
  const tstep = niceStep((view.t1-view.t0)/8);
  for(let t=Math.ceil(view.t0/tstep)*tstep; t<=view.t1; t+=tstep){
    const x=xOf(t); line(x,PAD_T,x,H-PAD_B); ctx.fillText(t.toFixed(0)+'s',x,H-8);
  }
  // grid — price
  ctx.textAlign='right';
  const pstep = niceStep(view.pw/8);
  for(let p=Math.ceil(pmin()/pstep)*pstep; p<=pmax(); p+=pstep){
    const y=yOf(p); line(PAD_L,y,W-PAD_R,y); ctx.fillText(p.toFixed(2),PAD_L-6,y+3);
  }

  // tile boundaries (flushed .tmht chunks)
  ctx.setLineDash([3,4]); ctx.strokeStyle='#2a3140';
  for(const tl of D.tiles){ line(xOf(tl.end),PAD_T,xOf(tl.end),H-PAD_B); }
  ctx.setLineDash([]);

  // heatmap columns
  const colW = Math.max(1.5,(0.5-view.t0*0)+ (D.frames[1]? (xOf(D.frames[1].t)-xOf(D.frames[0].t)) : 3));
  const rowH = Math.max(1.2, (TICK_PX()));
  ctx.save(); ctx.beginPath(); ctx.rect(PAD_L,PAD_T,plotW,plotH); ctx.clip();
  for(const f of D.frames){
    const x = xOf(f.t); if(x<PAD_L-colW||x>W+colW) continue;
    for(const c of f.c){
      const p = D.prices[c[0]]; if(p<pmin()-rowH||p>pmax()+rowH) continue;
      const y = yOf(p);
      if(c[1]>0){ ctx.fillStyle=heatColor(c[1],true);  ctx.fillRect(x-colW/2,y-rowH/2,colW,rowH);}
      if(c[2]>0){ ctx.fillStyle=heatColor(c[2],false); ctx.fillRect(x-colW/2,y-rowH/2,colW,rowH);}
    }
  }
  // trades
  for(const f of D.frames){
    const x=xOf(f.t); if(x<PAD_L||x>W-PAD_R) continue;
    for(const tr of f.tr){
      const p=D.prices[tr[0]]; if(p<pmin()||p>pmax()) continue;
      const y=yOf(p), buy=tr[2]===1, r=Math.min(4.5,1.2+tr[1]*0.12);
      ctx.fillStyle=buy?'#2fbf71':'#e0525f';
      ctx.globalAlpha=.9; tri(x,y,r,buy); ctx.globalAlpha=1;
    }
  }
  // mid price line
  ctx.strokeStyle='#f5c518'; ctx.lineWidth=1.4; ctx.beginPath();
  let first=true;
  for(const f of D.frames){
    const x=xOf(f.t), y=yOf(f.mid);
    if(first){ctx.moveTo(x,y);first=false}else ctx.lineTo(x,y);
  }
  ctx.stroke(); ctx.lineWidth=1;
  ctx.restore();

  // frame
  ctx.strokeStyle='#2a3140'; ctx.strokeRect(PAD_L+.5,PAD_T+.5,plotW-1,plotH-1);
  document.getElementById('pband').textContent =
    pmin().toFixed(2)+' – '+pmax().toFixed(2);
}
function TICK_PX(){ return (D.tickSize/view.pw)*(H-PAD_T-PAD_B); }
function line(a,b,c,d){ctx.beginPath();ctx.moveTo(a,b);ctx.lineTo(c,d);ctx.stroke();}
function tri(x,y,r,up){ctx.beginPath();
  if(up){ctx.moveTo(x,y-r);ctx.lineTo(x-r,y+r);ctx.lineTo(x+r,y+r);}
  else  {ctx.moveTo(x,y+r);ctx.lineTo(x-r,y-r);ctx.lineTo(x+r,y-r);}
  ctx.closePath();ctx.fill();}
function niceStep(raw){
  const p=Math.pow(10,Math.floor(Math.log10(raw)));
  for(const m of [1,2,2.5,5,10]) if(p*m>=raw) return p*m;
  return p*10;
}

// ---------- interaction ----------
let drag=null;
cv.addEventListener('mousedown',e=>{drag={x:e.clientX,y:e.clientY,t0:view.t0,t1:view.t1,pc:view.pc};});
window.addEventListener('mouseup',()=>drag=null);
window.addEventListener('mousemove',e=>{
  if(drag){
    const dx=e.clientX-drag.x, dt=dx/view.pps;
    view.t0=drag.t0-dt; view.t1=drag.t1-dt;
    view.pc=drag.pc+(e.clientY-drag.y)/(H-PAD_T-PAD_B)*view.pw;
    clampT(); draw();
  } else hover(e);
});
function clampT(){
  const span=view.t1-view.t0;
  if(view.t0<-span*.2){view.t0=-span*.2;view.t1=view.t0+span;}
  if(view.t1>D.duration+span*.2){view.t1=D.duration+span*.2;view.t0=view.t1-span;}
}
cv.addEventListener('wheel',e=>{
  e.preventDefault();
  const f=e.deltaY<0?1/1.15:1.15;
  if(e.ctrlKey){ view.pw*=f; }
  else{
    const t=tOf(e.offsetX);
    view.pps/=f;
    view.t0=t-(t-view.t0)*f; view.t1=t+(view.t1-t)*f; clampT();
  }
  draw();
},{passive:false});
window.addEventListener('keydown',e=>{if(e.key==='r'||e.key==='R'){view=defaultView();draw();}});

function hover(e){
  const x=e.offsetX,y=e.offsetY;
  if(x<PAD_L||y<PAD_T||y>H-PAD_B){tip.style.display='none';return;}
  const t=tOf(x),p=pOf(y);
  let best=null,bd=1e9;
  for(const f of D.frames){const d=Math.abs(f.t-t);if(d<bd){bd=d;best=f;}}
  if(!best||bd>1){tip.style.display='none';return;}
  let bp=null,bpd=1e9;
  for(const c of best.c){const pr=D.prices[c[0]],d=Math.abs(pr-p);if(d<bpd){bpd=d;bp=c;}}
  let s=`t=${best.t.toFixed(1)}s  mid=${best.mid.toFixed(2)}`;
  if(bp&&bpd<view.pw*0.02)
    s+=`\nprice ${D.prices[bp[0]].toFixed(2)}\nbid ${bp[1]} lot | ask ${bp[2]} lot`;
  tip.textContent=s; tip.style.display='block';
  tip.style.left=Math.min(W-150,x+14)+'px'; tip.style.top=(y+14)+'px';
}

window.addEventListener('resize',resize);
resize();
</script>
</body>
</html>
"""

if __name__ == "__main__":
    main()
