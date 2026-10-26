/* Tmap app simulator — replays real pipeline output (tiles .tmht export) */
(function(){
const D = window.TMAP_DATA;
const cv = document.getElementById('cv'), ctx = cv.getContext('2d');
const tip = document.getElementById('tip');
const PAD_L=60, PAD_B=24, PAD_T=8, PAD_R=8;
const FR = D.frames, DOMS = D.dom.filter(x=>x.bids.length||x.asks.length);
const TS = D.tickSize, DUR = D.duration;


// display grid step: aim ~90-140px per $ => TS*8
const GRID_TS = TS*8;
// ---- precompute: per-frame price band (where liquidity actually lives) ----
const BAND=[]; let midsSorted=[];
for(const f of FR){
  // quantize cell price indices onto a coarse display grid so rows stack up
  const q=Math.max(1,Math.round(GRID_TS/TS));
  const map={};
  for(const c of f.c){
    const qi=Math.round(c[0]/q)*q;
    const m=map[qi]; if(m){m[1]+=c[1];m[2]+=c[2];} else map[qi]=[qi,c[1],c[2]];
  }
  f.qc=Object.values(map);
  const tmap={};
  for(const tr of f.tr){
    const qi=Math.round(tr[0]/q)*q;
    const m=tmap[qi]; if(m){m[1]+=tr[1];} else tmap[qi]=[qi,tr[1],tr[2]];
  }
  f.qt=Object.values(tmap);
  if(f.c.length){
    let lo=Infinity,hi=-Infinity;
    for(const c of f.c){const p=D.pmin+c[0]*TS; if(p<lo)lo=p; if(p>hi)hi=p;}
    BAND.push([lo,hi]); midsSorted.push(f.mid);
  } else BAND.push(null);
}
midsSorted.sort((a,b)=>a-b);
const MEDMID=midsSorted[midsSorted.length>>1]||D.pmin;
// liquidity-weighted center: median of ALL cell prices across frames
const lps=[]; for(const f of FR) for(const c of f.c) lps.push(D.pmin+c[0]*TS);
lps.sort((a,b)=>a-b);
const LCENTER = lps.length? lps[lps.length>>1] : MEDMID;

// ---- state ----
let connected=false, recording=false, speed=4, playhead=0, lastReal=performance.now();
let view=null;
function defaultView(){
  // zoom to where the DOM liquidity actually sits (~+-1.4$ around median mid)
  // frame the densest liquidity cluster
  return {t0:-1,t1:DUR+1,pc:LCENTER,pw:1.5};
}
view=defaultView();

const xOf=t=>PAD_L+(t-view.t0)/(view.t1-view.t0)*(W-PAD_L-PAD_R);
const tOf=x=>view.t0+(x-PAD_L)/(W-PAD_L-PAD_R)*(view.t1-view.t0);
const yOf=p=>PAD_T+(view.pc+view.pw/2-p)/view.pw*(H-PAD_T-PAD_B);
const pOf=y=>view.pc+view.pw/2-(y-PAD_T)/(H-PAD_T-PAD_B)*view.pw;

let W=0,H=0,DPR=1;
function resize(){DPR=devicePixelRatio||1;W=cv.clientWidth;H=cv.clientHeight;
  cv.width=W*DPR;cv.height=H*DPR;ctx.setTransform(DPR,0,0,DPR,0,0);}
addEventListener('resize',()=>{resize();draw();});

function frameAt(t){ // nearest recorded frame <= t
  let lo=0,hi=FR.length-1,best=null;
  while(lo<=hi){const m=(lo+hi)>>1; if(FR[m].t<=t){best=FR[m];lo=m+1;}else hi=m-1;}
  return best;
}
function domAt(t){
  let lo=0,hi=DOMS.length-1,best=DOMS[0];
  while(lo<=hi){const m=(lo+hi)>>1; if(DOMS[m].t<=t){best=DOMS[m];lo=m+1;}else hi=m-1;}
  return best;
}

// typical frame spacing: median of positive diffs (robust to dropped frames)
const diffs=[]; for(let i=1;i<FR.length;i++){const d=FR[i].t-FR[i-1].t; if(d>0.01&&d<3)diffs.push(d);}
diffs.sort((a,b)=>a-b);
const FRAME_DT = diffs.length? diffs[diffs.length>>1] : 0.5;

// ---- heatmap draw ----
function heat(v,max,side){ // returns rgba string
  const k=Math.min(1,Math.sqrt(v/max));
  if(k<0.03)return null;
  const a=0.12+0.85*k;
  return side==='b'?`rgba(47,191,113,${a})`:`rgba(224,82,95,${a})`;
}
function draw(){
  ctx.clearRect(0,0,W,H);
  ctx.fillStyle='#0d1119';ctx.fillRect(0,0,W,H);
  let ML=1; for(const f of FR) for(const c of f.qc){if(c[1]>ML)ML=c[1];if(c[2]>ML)ML=c[2];}
  const maxLot=ML;
  // px per dollar at current zoom
  const ppx=(H-PAD_T-PAD_B)/view.pw;
  // choose display step so rows are >=2.2px tall
  let step=TS; while(step*ppx<2.2) step+=TS;
  const colPx=Math.max(2,(W-PAD_L-PAD_R)*FRAME_DT/(view.t1-view.t0)*0.92);
  for(const f of FR){
    if(f.t<view.t0-FRAME_DT||f.t>view.t1+FRAME_DT)continue;
    const x=xOf(f.t)-colPx/2;
    // liquidity cells within +-1.4$ of this frame's mid -> DOM depth ladder look
    for(const c of f.c){
      const p=D.pmin+c[0]*TS;
      if(f.mid && Math.abs(p-f.mid)>1.4) continue;
      const y=yOf(p); if(y<PAD_T-3||y>H-PAD_B+3)continue;
      const qi=Math.round(p/step)*step;
      const qy=yOf(qi);
      const rh=Math.max(2,step*ppx-0.6);
      const bid=c[1],ask=c[2];
      const hb=heat(bid,maxLot,'b'), ha=heat(ask,maxLot,'a');
      const half=Math.max(1,colPx/2);
      if(hb){ctx.fillStyle=hb;ctx.fillRect(x,qy-rh/2,half,rh);}
      if(ha){ctx.fillStyle=ha;ctx.fillRect(x+colPx-half,qy-rh/2,half,rh);}
    }
    // aggressor trade markers
    for(const tr of f.tr){
      const p=D.pmin+tr[0]*TS;
      if(f.mid && Math.abs(p-f.mid)>1.6) continue;
      const y=yOf(p), s=tr[1], buy=tr[2]==='buy'||tr[2]===1;
      const r=Math.min(7,1.8+Math.sqrt(s)*0.8);
      ctx.fillStyle=buy?'#37e08a':'#ff6b78';
      ctx.beginPath();
      if(buy){ctx.moveTo(x+colPx/2,y+r*0.8);ctx.lineTo(x+colPx/2-r*0.6,y-r*0.4);ctx.lineTo(x+colPx/2+r*0.6,y-r*0.4);}
      else{ctx.moveTo(x+colPx/2,y-r*0.8);ctx.lineTo(x+colPx/2-r*0.6,y+r*0.4);ctx.lineTo(x+colPx/2+r*0.6,y+r*0.4);}
      ctx.closePath();ctx.fill();
    }
  }
  // continuous mid-price polyline
  ctx.strokeStyle='rgba(245,197,24,.95)';ctx.lineWidth=1.6;ctx.beginPath();
  let started=false;
  for(const f of FR){ if(!f.mid)continue;
    const x=xOf(f.t),y=yOf(f.mid);
    if(!started){ctx.moveTo(x,y);started=true;}else ctx.lineTo(x,y);}
  ctx.stroke();
  // tile boundaries
  ctx.strokeStyle='#39414f';ctx.setLineDash([4,4]);ctx.lineWidth=1;
  for(const tl of D.tiles){for(const tt of [tl.start,tl.end]){const x=xOf(tt);
    ctx.beginPath();ctx.moveTo(x,PAD_T);ctx.lineTo(x,H-PAD_B);ctx.stroke();}}
  ctx.setLineDash([]);
  // axes
  ctx.fillStyle='#5c6572';ctx.font='10px ui-monospace,monospace';
  for(let i=0;i<=6;i++){const t=view.t0+i*(view.t1-view.t0)/6,x=xOf(t);
    ctx.fillText(t.toFixed(0)+'s',x-8,H-8);}
  for(let i=0;i<=8;i++){const p=view.pc-view.pw/2+i*view.pw/8,y=yOf(p);
    ctx.fillText(p.toFixed(2),4,y+3);}
  // playhead when live/replaying
  if(playhead>0){const x=xOf(playhead);
    ctx.strokeStyle='#f5c518';ctx.lineWidth=1;ctx.setLineDash([2,3]);
    ctx.beginPath();ctx.moveTo(x,PAD_T);ctx.lineTo(x,H-PAD_B);ctx.stroke();ctx.setLineDash([]);}
}

// ---- panels ----
const elAsks=document.getElementById('asks'), elBids=document.getElementById('bids'),
      elMid=document.getElementById('midline'), elTape=document.getElementById('tape');
function renderPanels(t){
  const d=domAt(t);
  const rows=(list,cls)=>list.slice(0,10).map(([p,q])=>
    `<div class="row ${cls}"><span class="q">${cls==='b'?(q?q.toFixed(2):''):''}</span><span class="p">${p.toFixed(2)}</span><span class="s">${cls==='a'?(q?q.toFixed(2):''):''}</span></div>`).join('');
  elAsks.innerHTML=rows(d.asks.slice().reverse(),'a');
  elBids.innerHTML=rows(d.bids,'b');
  elMid.textContent=d.mid?d.mid.toFixed(2):'—';
  elMid.style.color=d.mid?'#f5c518':'#5c6572';
  // tape: trades from frames up to t (last 14)
  const seen=[];
  for(const f of FR){if(f.t>t)break;
    for(const tr of f.tr){seen.push({t:f.t,p:(D.pmin+tr[0]*TS).toFixed(2),v:tr[1],s:tr[2]});}}
  elTape.innerHTML=seen.slice(-14).reverse().map(x=>
    `<div class="tr ${x.s==='sell'||x.s===0?'sell':'buy'}"><span>${x.t.toFixed(1)}s</span><span>${x.p}</span><span>${x.v.toFixed(2)} lot</span><span>${x.s==='sell'||x.s===0?'S':'B'}</span></div>`).join('');
  document.getElementById('nframe').textContent=FR.filter(f=>f.t<=t).length;
  document.getElementById('ntile').textContent=D.tiles.filter(x=>x.start<=t).length;
  document.getElementById('ntick').textContent=Math.round(t/DUR*9000);
}
document.getElementById('tiles').innerHTML=D.tiles.map(x=>
  `tile ${x.start.toFixed(0)}–${x.end.toFixed(0)}s · <b style="color:#d8dee9">${x.frames}</b> frames · ${(x.bytes/1024).toFixed(1)} KB`).join('<br>');

// ---- interaction ----
let drag=null;
cv.addEventListener('mousedown',e=>drag={x:e.offsetX,y:e.offsetY,t0:view.t0,t1:view.t1,pc:view.pc});
addEventListener('mouseup',()=>drag=null);
cv.addEventListener('mousemove',e=>{
  if(drag){const dt=(e.offsetX-drag.x)/(W-PAD_L-PAD_R)*(drag.t1-drag.t0);
    view.t0=drag.t0-dt;view.t1=drag.t1-dt;
    view.pc=drag.pc+(e.offsetY-drag.y)/(H-PAD_T-PAD_B)*view.pw;draw();return;}
  const f=frameAt(tOf(e.offsetX));
  if(f&&f.mid){tip.style.display='block';tip.style.left=(e.offsetX+14)+'px';tip.style.top=(e.offsetY+10)+'px';
    tip.textContent=`t=${f.t.toFixed(1)}s\nmid ${f.mid.toFixed(2)}\nlevels ${f.c.length}`;}
  else tip.style.display='none';
});
cv.addEventListener('mouseleave',()=>tip.style.display='none');
cv.addEventListener('wheel',e=>{e.preventDefault();
  const f=e.ctrlKey;const k=e.deltaY<0?0.85:1.18;
  if(f){view.pw*=k;}else{const t=tOf(e.offsetX);view.t0=t+(view.t0-t)*k;view.t1=t+(view.t1-t)*k;}
  draw();},{passive:false});
document.getElementById('reset').onclick=()=>{view=defaultView();draw();};

// ---- toolbar sim ----
const statEl=document.getElementById('stat');
document.getElementById('connect').onclick=function(){
  connected=!connected;
  this.textContent=connected?'■ Disconnect':'● Connect EA Bridge';
  statEl.innerHTML=connected?'bridge: <b>online :4712 · XAUUSD.m1 tick stream</b>':'bridge: <span style="color:#e0525f">offline</span>';
};
const recBtn=document.getElementById('rec');
recBtn.onclick=function(){
  recording=!recording;playhead=recording?0.1:0;
  this.textContent=recording?'⏸ Stop Recording':'▶ Start Recording';
  if(recording)tickLoop();
};
const spBtn=document.getElementById('speed');
spBtn.onclick=function(){speed=speed===1?4:speed===4?8:1;this.textContent='⏩ '+speed+'×';};

function tickLoop(){
  const now=performance.now();
  if(recording){playhead+=(now-lastReal)/1000*speed;
    if(playhead>=DUR){playhead=DUR;recording=false;recBtn.textContent='▶ Start Recording';}
    renderPanels(playhead);
    // auto-follow camera
    if(view.t1-playhead<5){view.t0=Math.max(0,playhead-(view.t1-view.t0));view.t1=view.t0+(view.t1-view.t0);}
    draw();}
  lastReal=now;
  if(recording||playhead>0)requestAnimationFrame(tickLoop);
}
resize();renderPanels(DUR);draw();
})();
