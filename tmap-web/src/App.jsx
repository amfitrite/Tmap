import React, { useRef, useEffect, useState, useCallback } from "react";
import DATA from "./data.json";

/* ────────────────────────────────────────────────────────────────────
   Tmap Web Simulator — replika UI aplikasi PySide6 Tmap.
   Data: hasil pipeline asli RecordingSession → DOMCache → TileStore.
   frames[i] = { t, mid, c:[[lev,bidLot,askLot],...], tr:[[lev,vol,buyFlag],...] }
   harga(lev) = pmin + lev*tickSize ; nlev level.
   Konvensi render: tiap snapshot bertahan sampai frame berikutnya
   (heatmap L2 klasik) sehingga pita likuiditas padat & kontinu.
──────────────────────────────────────────────────────────────────── */

const TS = DATA.tickSize, PMIN = DATA.pmin, NLEV = DATA.nlev;
const FRAMES = DATA.frames, DOMS = DATA.dom, TILES = DATA.tiles;
const priceOf = (lev) => PMIN + lev * TS;
const levOf = (p) => Math.round((p - PMIN) / TS);

/* ── Pra-pemrosesan: akumulasi temporal + merge grid kasar ──
   Grid $0.01 terlalu halus untuk viewport; kita kuantisasi ke
   GRID=$0.08 dan akumulasikan rolling-window antar frame (decay),
   seperti aggregation yang dipakai UI PySide6. */
const GRID = 8;                       // 8 tick ($0.08) per baris render
const DECAY = 0.62;                   // peluruhan bobot antar frame

/* ── Normalisasi per-frame ala Bookmap "volume-in-price" ──
   Setiap frame di-scale sehingga rata-rata intensitasnya seragam —
   dinding likuiditas besar tetap dominan, tapi level kecil tidak
   tenggelam jadi piksel samar. Inilah kunci visual "mahal". */
const GFRAMES = (() => {
  const acc = new Map();              // gkey -> [bidLot, askLot]
  const raw = [];
  for (let i = 0; i < FRAMES.length; i++) {
    const f = FRAMES[i];
    for (const k of Object.keys(acc)) {
      const v = acc[k]; v[0] *= DECAY; v[1] *= DECAY;
      if (v[0] < 0.05 && v[1] < 0.05) delete acc[k];
    }
    for (const [lev, b, a] of f.c) {
      const gk = Math.floor(lev / GRID);
      let cell = acc[gk];
      if (!cell) cell = acc[gk] = [0, 0];
      cell[0] += b; cell[1] += a;
    }
    const cells = [];
    let sum = 0;
    for (const gk in acc) {
      const [b, a] = acc[gk];
      if (b > 0.05 || a > 0.05) { cells.push([+gk, b, a]); sum += b + a; }
    }
    cells.sort((x, y) => x[0] - y[0]);
    raw.push({ t: f.t, mid: f.mid, c: cells, tr: f.tr, mean: cells.length ? sum / cells.length : 1 });
  }
  // target normalisasi: median dari rata-rata per frame
  const means = raw.map(r => r.mean).sort((a, b) => a - b);
  const TARGET = means[means.length >> 1] || 1;
  return raw.map(r => ({ ...r, norm: TARGET / r.mean }));
})();
const MAXNORM = (() => { let m = 0; for (const f of GFRAMES) for (const [, b, a] of f.c) { const bb = b * f.norm, aa = a * f.norm; if (bb > m) m = bb; if (aa > m) m = aa; } return m; })();

/* ── Palet heatmap ala Bloomberg/pro-grade: teal→cyan→putih & crimson→orange→emas ──
   Multi-stop gradient diskrit (7 tingkat) — bukan alpha solid, jadi
   low-liquidity tetap terlihat jelas dan wall terasa "menyala". */
const BID_STOPS = [
  [0.00, [ 34,  74,  92]], [0.18, [ 22, 110, 122]], [0.38, [ 26, 160, 150]],
  [0.58, [ 52, 200, 168]], [0.76, [118, 232, 190]], [0.90, [186, 248, 220]], [1.00, [240, 255, 248]],
];
const ASK_STOPS = [
  [0.00, [ 86,  34,  52]], [0.18, [130,  36,  52]], [0.38, [186,  46,  60]],
  [0.58, [228,  72,  66]], [0.76, [250, 130,  84]], [0.90, [255, 190, 110]], [1.00, [255, 240, 190]],
];
function ramp(stops, t) {
  t = Math.max(0, Math.min(1, t));
  for (let i = 1; i < stops.length; i++) {
    if (t <= stops[i][0]) {
      const [t0, c0] = stops[i - 1], [t1, c1] = stops[i];
      const k = (t - t0) / (t1 - t0 || 1);
      return `rgb(${Math.round(c0[0] + (c1[0] - c0[0]) * k)},${Math.round(c0[1] + (c1[1] - c0[1]) * k)},${Math.round(c0[2] + (c1[2] - c0[2]) * k)})`;
    }
  }
  const c = stops[stops.length - 1][1];
  return `rgb(${c[0]},${c[1]},${c[2]})`;
}
function heatColor(lot, side, norm) {
  if (lot <= 0) return null;
  const v = (lot * norm) / MAXNORM;
  const t = 0.10 + 0.90 * Math.pow(v, 0.42);       // gamma → level rendah tetap kelihatan
  return ramp(side === "b" ? BID_STOPS : ASK_STOPS, t);
}

// default view: waktu penuh, band harga ±0.85$ di sekitar median mid
const MEDMID = (() => { const m = FRAMES.map(f => f.mid).sort((a,b)=>a-b); return m[m.length>>1]; })();
const DEF_VIEW = { t0: 0, t1: DATA.duration, p0: MEDMID - 0.85, p1: MEDMID + 0.85 };

export default function App() {
  const cvsRef = useRef(null), wrapRef = useRef(null), tipRef = useRef(null);
  const viewRef = useRef({ ...DEF_VIEW });
  const dragRef = useRef(null);
  const [connected, setConnected] = useState(false);
  const [recording, setRecording] = useState(false);
  const [speed, setSpeed] = useState(4);
  const playRef = useRef({ t: 0, raf: 0, last: 0 });
  const [head, setHead] = useState(DATA.duration);      // playhead (s)
  const [frameIdx, setFrameIdx] = useState(FRAMES.length - 1);
  const speedRef = useRef(speed); speedRef.current = speed;
  const recRef = useRef(recording); recRef.current = recording;

  /* ── render core ── */
  const draw = useCallback(() => {
    const cvs = cvsRef.current, wrap = wrapRef.current;
    if (!cvs || !wrap) return;
    const dpr = window.devicePixelRatio || 1;
    const W = wrap.clientWidth, H = wrap.clientHeight;
    if (cvs.width !== W * dpr || cvs.height !== H * dpr) { cvs.width = W * dpr; cvs.height = H * dpr; }
    const g = cvs.getContext("2d");
    g.setTransform(dpr, 0, 0, dpr, 0, 0);
    // latar gradien halus (premium feel, bukan flat #131722 polos)
    const bg = g.createLinearGradient(0, 0, 0, H);
    bg.addColorStop(0, "#12161f"); bg.addColorStop(1, "#0b0e15");
    g.fillStyle = bg; g.fillRect(0, 0, W, H);

    const v = viewRef.current;
    const AX = 62, AY = 24;                    // lebar sumbu harga / tinggi sumbu waktu
    const pw = W - AX, ph = H - AY;
    const X = (t) => ((t - v.t0) / (v.t1 - v.t0)) * pw;
    const Y = (p) => ph - ((p - v.p0) / (v.p1 - v.p0)) * ph;

    const upto = recRef.current ? headRefV() : Infinity;   // replay cut-off

    // grid halus ala terminal pro
    {
      const pspan = v.p1 - v.p0;
      const pstep = pspan > 3 ? 0.5 : pspan > 1.2 ? 0.2 : pspan > 0.5 ? 0.1 : 0.02;
      g.strokeStyle = "rgba(42,46,57,.35)"; g.lineWidth = 1;
      for (let p = Math.ceil(v.p0 / pstep) * pstep; p <= v.p1; p += pstep) {
        const y = Math.round(Y(p)) + .5; g.beginPath(); g.moveTo(0, y); g.lineTo(pw, y); g.stroke();
      }
      const tspan = v.t1 - v.t0, tstep = tspan > 40 ? 10 : tspan > 15 ? 5 : tspan > 6 ? 2 : 1;
      for (let t = Math.ceil(v.t0 / tstep) * tstep; t <= v.t1; t += tstep) {
        const x = Math.round(X(t)) + .5; g.beginPath(); g.moveTo(x, 0); g.lineTo(x, ph); g.stroke();
      }
    }

    // frames yang tampil di viewport (GFRAMES: grid $0.08 terakumulasi & ternormalisasi)
    for (let i = 0; i < GFRAMES.length; i++) {
      const f = GFRAMES[i];
      if (f.t > upto) break;
      const t1 = i + 1 < GFRAMES.length ? Math.min(GFRAMES[i + 1].t, upto) : upto;
      const x0 = X(f.t), x1 = X(t1);
      if (x1 < 0 || x0 > pw) continue;
      const cx0 = Math.max(x0, 0), cx1 = Math.min(x1, pw);
      if (cx1 - cx0 < 0.5) continue;
      const gp = GRID * TS;                                 // tinggi 1 baris render ($0.08)
      const rowH = Math.max(2, (gp / (v.p1 - v.p0)) * ph);
      for (const [gk, b, a] of f.c) {
        const p = PMIN + gk * gp;                           // harga bawah baris grid
        if (p + gp < v.p0 || p > v.p1) continue;
        const y = Y(p + gp / 2);
        const colB = heatColor(b, "b", f.norm);
        const colA = heatColor(a, "a", f.norm);
        // sel bid & ask pada level yang sama → blend vertikal sederhana: gambar ask lalu bid di paruh atas/bawah bila keduanya besar
        if (colB && colA) {
          g.fillStyle = colA; g.fillRect(cx0, y - rowH / 2, cx1 - cx0, rowH);
          g.fillStyle = colB; g.globalAlpha = 0.55 + 0.45 * Math.min(1, b / (a + b));
          g.fillRect(cx0, y - rowH / 2, cx1 - cx0, rowH);
          g.globalAlpha = 1;
        } else {
          const col = colB || colA;
          if (col) { g.fillStyle = col; g.fillRect(cx0, y - rowH / 2, cx1 - cx0, rowH); }
        }
      }
      // agresor trades — bubble CVD ala Bookmap (glow + ring)
      for (const [lev, vol, buy] of f.tr) {
        const p = priceOf(lev);
        if (p < v.p0 || p > v.p1) continue;
        const x = (X(f.t) + X(t1)) / 2, y = Y(p);
        const r = Math.min(9, 2 + Math.sqrt(vol) * 1.6);
        const cBase = buy ? "38,166,154" : "239,83,80";
        const rg = g.createRadialGradient(x, y, 0, x, y, r * 2.2);
        rg.addColorStop(0, `rgba(${cBase},.9)`); rg.addColorStop(.55, `rgba(${cBase},.35)`); rg.addColorStop(1, `rgba(${cBase},0)`);
        g.fillStyle = rg; g.beginPath(); g.arc(x, y, r * 2.2, 0, 7); g.fill();
        g.fillStyle = buy ? "#0f6f64" : "#b3312e";
        g.beginPath(); g.arc(x, y, r, 0, 7); g.fill();
        g.strokeStyle = buy ? "#7fe0cf" : "#ffb4a8"; g.lineWidth = 1;
        g.beginPath(); g.arc(x, y, r, 0, 7); g.stroke();
        if (r > 5.5) {   // label volume untuk whale trade
          g.fillStyle = "#fff"; g.font = "bold 8px Inter, sans-serif"; g.textAlign = "center";
          g.fillText(vol.toFixed(1), x, y + 2.6);
        }
      }
    }

    // polyline mid-price dengan glow layering
    const drawMid = () => {
      const path = () => {
        g.beginPath(); let started = false;
        for (const f of FRAMES) {
          if (f.t > upto) break;
          const x = X(f.t), y = Y(f.mid);
          if (!started) { g.moveTo(x, y); started = true; } else g.lineTo(x, y);
        }
      };
      g.lineJoin = "round"; g.lineCap = "round";
      path(); g.strokeStyle = "rgba(247,201,72,.14)"; g.lineWidth = 6; g.stroke();
      path(); g.strokeStyle = "rgba(247,201,72,.35)"; g.lineWidth = 3; g.stroke();
      path(); g.strokeStyle = "#ffd35c"; g.lineWidth = 1.4; g.stroke();
    };
    drawMid();

    // batas tile .tmht
    g.strokeStyle = "rgba(120,123,134,.35)"; g.setLineDash([3, 5]); g.lineWidth = 1;
    for (const tl of TILES) { const x = X(tl.start); if (x >= 0 && x <= pw) { g.beginPath(); g.moveTo(x, 0); g.lineTo(x, ph); g.stroke(); } }
    g.setLineDash([]);

    // playhead saat replay — garis neon + panah
    if (recRef.current) {
      const x = X(playRef.current.t);
      g.strokeStyle = "rgba(88,166,255,.25)"; g.lineWidth = 5;
      g.beginPath(); g.moveTo(x, 0); g.lineTo(x, ph); g.stroke();
      g.strokeStyle = "#58a6ff"; g.lineWidth = 1.4;
      g.beginPath(); g.moveTo(x, 0); g.lineTo(x, ph); g.stroke();
      g.fillStyle = "#58a6ff"; g.beginPath();
      g.moveTo(x - 5, 0); g.lineTo(x + 5, 0); g.lineTo(x, 8); g.closePath(); g.fill();
    }

    // ── axes ──
    const axBg = g.createLinearGradient(pw, 0, W, 0);
    axBg.addColorStop(0, "#161b26"); axBg.addColorStop(1, "#1a2030");
    g.fillStyle = axBg; g.fillRect(pw, 0, AX, H);
    g.fillStyle = "#161b26"; g.fillRect(0, ph, W, AY);
    g.strokeStyle = "rgba(60,66,82,.9)"; g.beginPath(); g.moveTo(pw + .5, 0); g.lineTo(pw + .5, ph); g.moveTo(0, ph + .5); g.lineTo(W, ph + .5); g.stroke();
    g.fillStyle = "#8b93a7"; g.font = "10px 'JetBrains Mono', ui-monospace, monospace"; g.textAlign = "left";
    const pspan = v.p1 - v.p0;
    const pstep = pspan > 3 ? 0.5 : pspan > 1.2 ? 0.2 : pspan > 0.5 ? 0.1 : 0.02;
    for (let p = Math.ceil(v.p0 / pstep) * pstep; p <= v.p1; p += pstep) {
      const y = Y(p); g.fillText(p.toFixed(2), pw + 7, y + 3);
    }
    g.textAlign = "center";
    const tspan = v.t1 - v.t0, tstep = tspan > 40 ? 10 : tspan > 15 ? 5 : tspan > 6 ? 2 : 1;
    for (let t = Math.ceil(v.t0 / tstep) * tstep; t <= v.t1; t += tstep) {
      const x = X(t); g.fillText(t + "s", x, ph + 15);
    }
    // watermark brand halus
    g.textAlign = "right"; g.fillStyle = "rgba(120,127,144,.35)"; g.font = "600 11px Inter, sans-serif";
    g.fillText(`TMAP · ${DATA.symbol} · L2 RECONSTRUCTED`, pw - 10, ph - 8);
  }, []);

  const headRefV = () => playRef.current.t;

  /* ── RAF loop replay ── */
  useEffect(() => {
    let raf;
    const tickLoop = (now) => {
      if (recRef.current) {
        const P = playRef.current;
        if (P.last) P.t += ((now - P.last) / 1000) * speedRef.current;
        P.last = now;
        if (P.t >= DATA.duration) { P.t = DATA.duration; setRecording(false); }
        setHead(P.t);
        // update frame index untuk sidebar
        let idx = 0;
        for (let i = 0; i < FRAMES.length; i++) if (FRAMES[i].t <= P.t) idx = i; else break;
        setFrameIdx(idx);
        // auto-follow camera kalau playhead keluar viewport
        const v = viewRef.current;
        if (P.t > v.t1 - (v.t1 - v.t0) * 0.08) {
          const span = v.t1 - v.t0;
          v.t0 = P.t - span * 0.75; v.t1 = P.t + span * 0.25;
        }
        draw();
      }
      raf = requestAnimationFrame(tickLoop);
    };
    raf = requestAnimationFrame(tickLoop);
    return () => cancelAnimationFrame(raf);
  }, [draw]);

  useEffect(() => { draw(); }, [draw, connected, head]);

  /* ── interactions: pan / zoom / hover ── */
  useEffect(() => {
    const wrap = wrapRef.current;
    const onDown = (e) => { dragRef.current = { x: e.clientX, y: e.clientY, v: { ...viewRef.current } }; };
    const onMove = (e) => {
      const D = dragRef.current;
      if (D) {
        const v = viewRef.current;
        const rect = wrap.getBoundingClientRect();
        const pw = rect.width - 58, ph = rect.height - 22;
        v.t0 = D.v.t0 - ((e.clientX - D.x) / pw) * (D.v.t1 - D.v.t0);
        v.t1 = D.v.t1 - ((e.clientX - D.x) / pw) * (D.v.t1 - D.v.t0);
        v.p0 = D.v.p0 + ((e.clientY - D.y) / ph) * (D.v.p1 - D.v.p0);
        v.p1 = D.v.p1 + ((e.clientY - D.y) / ph) * (D.v.p1 - D.v.p0);
        draw();
      } else {
        // tooltip
        const rect = wrap.getBoundingClientRect();
        const mx = e.clientX - rect.left, my = e.clientY - rect.top;
        const tip = tipRef.current;
        if (mx < rect.width - 58 && my < rect.height - 22) {
          const v = viewRef.current;
          const t = v.t0 + (mx / (rect.width - 58)) * (v.t1 - v.t0);
          const p = v.p0 + ((rect.height - 22 - my) / (rect.height - 22)) * (v.p1 - v.p0);
          let fi = 0; for (let i = 0; i < FRAMES.length; i++) if (FRAMES[i].t <= t) fi = i; else break;
          const f = FRAMES[fi]; const lev = levOf(p);
          const cell = f.c.find(c => c[0] === lev);
          tip.style.display = "block";
          tip.style.left = Math.min(mx + 14, rect.width - 170) + "px";
          tip.style.top = (my + 10) + "px";
          tip.innerHTML = `<b style="color:#f7c948">${p.toFixed(2)}</b> @ ${f.t.toFixed(1)}s<br>` +
            `bid ${(cell ? cell[1] : 0).toFixed(2)} lot <span style="color:#26a69a">▮</span>&nbsp;` +
            `ask ${(cell ? cell[2] : 0).toFixed(2)} lot <span style="color:#ef5350">▮</span><br>` +
            `<span style="color:#787b86">mid ${f.mid.toFixed(2)}</span>`;
        } else tip.style.display = "none";
      }
    };
    const onUp = () => { dragRef.current = null; };
    const onWheel = (e) => {
      e.preventDefault();
      const v = viewRef.current, rect = wrap.getBoundingClientRect();
      const frac = Math.max(0, Math.min(1, (e.ctrlKey ? (rect.height - 22 - (e.clientY - rect.top)) / (rect.height - 22) : (e.clientX - rect.left) / (rect.width - 58))));
      const k = e.deltaY > 0 ? 1.18 : 1 / 1.18;
      if (e.ctrlKey) { const pc = v.p0 + frac * (v.p1 - v.p0); v.p0 = pc - (pc - v.p0) * k; v.p1 = pc + (v.p1 - pc) * k; }
      else { const tc = v.t0 + frac * (v.t1 - v.t0); v.t0 = tc - (tc - v.t0) * k; v.t1 = tc + (v.t1 - tc) * k; }
      draw();
    };
    const onKey = (e) => { if (e.key === "r" || e.key === "R") { Object.assign(viewRef.current, DEF_VIEW); draw(); } };
    wrap.addEventListener("mousedown", onDown);
    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
    wrap.addEventListener("wheel", onWheel, { passive: false });
    window.addEventListener("keydown", onKey);
    window.addEventListener("resize", draw);
    return () => {
      wrap.removeEventListener("mousedown", onDown);
      window.removeEventListener("mousemove", onMove);
      window.removeEventListener("mouseup", onUp);
      wrap.removeEventListener("wheel", onWheel);
      window.removeEventListener("keydown", onKey);
      window.removeEventListener("resize", draw);
    };
  }, [draw]);

  const startRec = () => {
    if (recording) { setRecording(false); playRef.current.last = 0; return; }
    playRef.current.t = 0; playRef.current.last = 0;
    Object.assign(viewRef.current, { ...DEF_VIEW, t1: Math.min(DEF_VIEW.t1, 15) });
    setRecording(true);
  };
  const resetView = () => { Object.assign(viewRef.current, DEF_VIEW); draw(); };

  /* ── sidebar derived state ── */
  const dom = DOMS[Math.min(frameIdx, DOMS.length - 1)];
  const bids = [...dom.bids].sort((a, b) => b[0] - a[0]).slice(0, 8);
  const asks = [...dom.asks].sort((a, b) => a[0] - b[0]).slice(0, 8);
  const maxOb = Math.max(0.01, ...bids.map(x => x[1]), ...asks.map(x => x[1]));
  const visibleTiles = TILES.filter(t => t.start <= (recording ? head : DATA.duration)).length;
  const tape = [];
  for (let i = frameIdx; i >= 0 && tape.length < 14; i--)
    for (const [lev, vol, buy] of FRAMES[i].tr) { tape.push({ t: FRAMES[i].t, p: priceOf(lev), v: vol, buy }); if (tape.length >= 14) break; }
  // delta mid untuk flash harga
  const prevMid = DOMS[Math.max(0, Math.min(frameIdx, DOMS.length - 1) - 1)].mid;
  const up = dom.mid >= prevMid;

  return (
    <div className="app">
      {/* Toolbar */}
      <div className="toolbar">
        <span className="logo"><i className="fa-solid fa-fire-flame-curved" />TMAP</span>
        <span className="divider" />
        <button className="btn symbol"><i className="fa-solid fa-coins" />{DATA.symbol}<i className="fa-solid fa-chevron-down chev" /></button>
        <button className={"btn" + (connected ? " primary" : "")} onClick={() => setConnected(!connected)}>
          <i className="fa-solid fa-plug" />{connected ? "Disconnect" : "Connect EA Bridge"}
        </button>
        <button className={"btn rec" + (recording ? " live" : "")} onClick={startRec}>
          <i className={"fa-solid " + (recording ? "fa-stop" : "fa-play")} />{recording ? "Stop" : "Start Recording"}
        </button>
        <div className="seg">
          {[1, 4, 8].map(s => (
            <button key={s} className={"seg-btn" + (speed === s ? " on" : "")} onClick={() => setSpeed(s)}>{s}×</button>
          ))}
        </div>
        <button className="btn ghost" onClick={resetView}><i className="fa-solid fa-rotate-left" /></button>
        <span className="status">
          <span className={"dot" + (connected ? " on" : "")} />
          {connected ? "bridge online · :4712" : "bridge offline"} · sim feed {DATA.symbol}
        </span>
      </div>

      {/* Main */}
      <div className="main">
        <div className="chart-wrap" ref={wrapRef}>
          <canvas className="hm" ref={cvsRef} />
          <div className="tooltip" ref={tipRef} />
          {/* HUD kiri-atas ala terminal pro */}
          <div className="hud">
            <span className="hud-sym">{DATA.symbol}</span>
            <span className={"hud-mid" + (up ? " up" : " dn")}>{dom.mid.toFixed(2)}</span>
            <span className="hud-tag">L2 RECONSTRUCTED · SIM</span>
          </div>
        </div>

        <div className="side">
          <div className="panel mid-panel">
            <h3>Mid Price</h3>
            <div className={"mid-big" + (up ? " up" : " dn")}>{dom.mid.toFixed(2)}</div>
            <div className="mid-sub">spread {(Math.abs(dom.ask - dom.bid)).toFixed(2)} $ · t={dom.t.toFixed(1)}s</div>
          </div>
          <div className="panel ob-panel">
            <h3><i className="fa-solid fa-layer-group" /> Order Book L2</h3>
            <div className="ob">
              <div className="ob-col">
                <div className="ob-head">BID</div>
                {bids.map(([p, l], i) => (
                  <div className="ob-row bid" key={"b" + i}>
                    <div className="bar" style={{ width: (l / maxOb * 100) + "%" }} />
                    <span>{p.toFixed(2)}</span><span>{l.toFixed(2)}</span>
                  </div>
                ))}
              </div>
              <div className="ob-col">
                <div className="ob-head ask-h">ASK</div>
                {asks.map(([p, l], i) => (
                  <div className="ob-row ask" key={"a" + i}>
                    <div className="bar" style={{ width: (l / maxOb * 100) + "%" }} />
                    <span>{p.toFixed(2)}</span><span>{l.toFixed(2)}</span>
                  </div>
                ))}
              </div>
            </div>
          </div>
          <div className="panel tape-panel">
            <h3><i className="fa-solid fa-clock-rotate-left" /> Time &amp; Sales</h3>
            <div className="tape">
              <table>
                <tbody>
                  {tape.map((r, i) => (
                    <tr key={i}>
                      <td className="t">{r.t.toFixed(1)}s</td>
                      <td className={r.buy ? "buy" : "sell"}>{r.p.toFixed(2)}</td>
                      <td>{r.v.toFixed(2)}</td>
                      <td className={r.buy ? "buy" : "sell"}>{r.buy ? "▲ BUY" : "▼ SELL"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
          <div className="panel tiles-panel">
            <h3><i className="fa-solid fa-database" /> Tile Store <span className="chip">.tmht</span></h3>
            <div className="tiles-list">
              {TILES.map((tl, i) => (
                <div className={"tile-item" + (tl.start <= (recording ? head : DATA.duration) ? "" : " dim")} key={i}>
                  <b><i className="fa-regular fa-file" /> tile_{i}.tmht</b>
                  <span>{tl.start.toFixed(0)}–{tl.end.toFixed(0)}s · {(tl.bytes / 1024).toFixed(0)} KB</span>
                </div>
              ))}
            </div>
          </div>
        </div>
      </div>

      {/* Footer */}
      <div className="footer">
        <div className="legend">
          <span className="lg"><span className="sw sw-bid" />Bid liquidity</span>
          <span className="lg"><span className="sw sw-ask" />Ask liquidity</span>
          <span className="lg"><span className="sw sw-mid" />Mid price</span>
        </div>
        <span className="hint">drag=pan · wheel=zoom waktu · ctrl+wheel=zoom harga · R=reset</span>
        <div className="counters">
          <span>tiles <b>{visibleTiles}/{TILES.length}</b></span>
          <span>frames <b>{frameIdx + 1}/{FRAMES.length}</b></span>
          <span>t <b>{(recording ? head : DATA.duration).toFixed(1)}s</b></span>
        </div>
      </div>
    </div>
  );
}
