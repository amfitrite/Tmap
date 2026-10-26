"""
Tmap — Pro Liquidity Heatmap (pure Python, zero chrome).

Full-bleed render: no title bar, no toolbar, no window frame — the image IS
the app. Bookmap-grade visuals: 7-stop glow palettes, log-intensity scaling,
temporal persistence with decay, glowing mid-price line, whale-trade bubbles.

Pipeline (identical to the live app, headless & time-accelerated):
    synthetic ticks -> RecordingSession (DOMCache -> frames -> tiles on disk)
                   -> TileStore.query_window  (viewport query)
                   -> matplotlib render -> PNG

Usage:
    python pro_heatmap2.py [duration_seconds] [out.png]
"""
import random
import sys
import tempfile

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.colors import LinearSegmentedColormap, LogNorm

from tmap.core.heatmap_store import TileStore
from tmap.core.types import Tick, TradeSide

DURATION_S = float(sys.argv[1]) if len(sys.argv) > 1 else 60.0
OUT = sys.argv[2] if len(sys.argv) > 2 else "pro_heatmap.png"
TICK_SIZE = 0.01
FRAME_INTERVAL = 0.5
GRID = 16           # display grid: 16 ticks = $0.16 per row (readable bands)
BAND = 30           # price band: +/- this many grid rows around median mid


# ---------------------------------------------------------------- data ----
def synth_ticks(t0: float, duration: float, n: int = 9000):
    rng = random.Random(42)
    price = 2750.0
    for i in range(n):
        ts = t0 + duration * i / n
        step = rng.choice([-3, -2, -1, -1, 0, 0, 1, 1, 2, 3]) * TICK_SIZE
        if rng.random() < 0.01:                      # impulse move
            step *= rng.choice([8, 12])
        price = max(TICK_SIZE, round((price + step) / TICK_SIZE) * TICK_SIZE)
        spread = rng.choice([1, 2, 3]) * TICK_SIZE
        best_bid = round(price - spread / 2.0, 4)
        best_ask = round(price + spread / 2.0, 4)
        side = TradeSide.BUY if rng.random() > 0.48 else TradeSide.SELL
        trade_price = best_ask if side == TradeSide.BUY else best_bid
        vol = rng.uniform(25, 150) if rng.random() < 0.05 else rng.expovariate(0.5) + 0.1
        yield Tick(timestamp=ts, price=trade_price, volume=round(vol, 2),
                   side=side, bid=best_bid, ask=best_ask)


# ------------------------------------------------------------- render ----
def make_cmaps():
    bid = LinearSegmentedColormap.from_list(
        "bid", ["#04060b", "#06303a", "#0a5c58", "#0f8f6e", "#35c97e",
               "#8ce878", "#d9ff9d", "#ffffff"], N=256)
    ask = LinearSegmentedColormap.from_list(
        "ask", ["#04060b", "#330a24", "#67113a", "#a01c40", "#d8383a",
                "#ff6f3d", "#ffb45e", "#fff3c4"], N=256)
    return bid, ask


def main():
    root = tempfile.mkdtemp(prefix="tmap_pro_")
    store = TileStore(root=root, tile_seconds=10.0, frame_interval_s=FRAME_INTERVAL)
    session = store.open_session(symbol="XAUUSD", tick_size=TICK_SIZE)

    t0 = 1_760_000_000.0
    for tk in synth_ticks(t0, DURATION_S):
        session.ingest(tk)
    tiles = session.finish()
    t_end = t0 + DURATION_S

    frames = [f for f in store.query_window("XAUUSD", t0, t_end,
                                            price_lo=2700.0, price_hi=2800.0)
              if f.cells]
    if not frames:
        raise SystemExit("no frames — check pipeline")

    # ---- build dense grids --------------------------------------------
    # restrict to the readable price band around median mid FIRST
    med = np.median([f.mid_price for f in frames])
    band = BAND * GRID * TICK_SIZE / 2.0            # +/- dollars around mid
    lo_p, hi_p = med - band, med + band
    idxs_all = sorted({pi for f in frames for pi in f.cells
                       if lo_p <= pi * TICK_SIZE <= hi_p})
    pmin, pmax = idxs_all[0] * TICK_SIZE, idxs_all[-1] * TICK_SIZE
    gidx = {pi: j for j, pi in enumerate(idxs_all)}          # raw idx -> row
    ny = len(idxs_all)
    ncol = len(frames)

    def nearest_row(pi):
        """Map a raw price index to its band row (None if outside band)."""
        j = gidx.get(pi)
        if j is not None:
            return j
        # binary search on the kept index list; drop if beyond edge tolerance
        import bisect
        j = bisect.bisect_left(idxs_all, pi)
        if 0 < j < len(idxs_all):
            lo_, hi_ = idxs_all[j - 1], idxs_all[j]
            return j - 1 if pi - lo_ <= hi_ - pi else j
        if j == 0 and abs(idxs_all[0] - pi) <= GRID:
            return 0
        if j == len(idxs_all) and abs(idxs_all[-1] - pi) <= GRID:
            return len(idxs_all) - 1
        return None

    # per-frame max-normalised intensity (rolling window keeps walls alive)
    raw_b = np.zeros((ny, ncol))
    raw_a = np.zeros((ny, ncol))
    mids_t, mids_p = [], []
    buy_pts, sell_pts = [], []
    for j, f in enumerate(frames):
        for pi, c in f.cells.items():
            r = nearest_row(pi)
            if r is None:
                continue
            raw_b[r, j] = c.bid
            raw_a[r, j] = c.ask
        mids_t.append(f.timestamp - t0)
        mids_p.append(f.mid_price)
        for (pi, v, s) in f.trades:
            (buy_pts if s == 1 else sell_pts).append(
                (f.timestamp - t0, pi * TICK_SIZE, v))

    # temporal persistence: hold each column until next frame, then decay
    W = 1600
    edges = [0.0] + [(frames[i].timestamp + frames[i + 1].timestamp) / 2 - t0
                     for i in range(ncol - 1)] + [DURATION_S]
    img_b = np.zeros((ny, W))
    img_a = np.zeros((ny, W))
    xpix = lambda t: int(np.clip(t / DURATION_S * W, 0, W))
    for j in range(ncol):
        x0, x1 = xpix(edges[j]), xpix(edges[j + 1])
        if x1 <= x0:
            x1 = min(W, x0 + 1)
        img_b[:, x0:x1] = raw_b[:, j:j + 1]
        img_a[:, x0:x1] = raw_a[:, j:j + 1]

    # smooth horizontally a touch (motion-blur feel) + merge to GRID rows
    k = np.array([0.15, 0.7, 0.15])
    img_b = np.apply_along_axis(lambda m: np.convolve(m, k, mode="same"), 1, img_b)
    img_a = np.apply_along_axis(lambda m: np.convolve(m, k, mode="same"), 1, img_a)

    rows_per_grid = GRID
    ny_pad = int(np.ceil(ny / rows_per_grid) * rows_per_grid)
    if ny_pad > ny:
        pad = ((0, ny_pad - ny), (0, 0))
        img_b = np.pad(img_b, pad)
        img_a = np.pad(img_a, pad)
    ny_g = ny_pad // rows_per_grid
    gb = np.max(img_b.reshape(ny_g, rows_per_grid, W), axis=1)
    ga = np.max(img_a.reshape(ny_g, rows_per_grid, W), axis=1)
    h = ny_g

    vmax = max(gb.max(), ga.max(), 1e-6)
    norm = LogNorm(vmin=max(vmax * 0.006, 1e-4), vmax=vmax * 1.05, clip=True)

    cmap_bid, cmap_ask = make_cmaps()
    y_lo = pmin
    y_hi = pmin + h * GRID * TICK_SIZE

    # ---------------- figure: full bleed, zero chrome -------------------
    fig = plt.figure(figsize=(16, 9), dpi=100)
    fig.patch.set_facecolor("#04060b")
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_facecolor("#04060b")
    ax.set_xlim(0, DURATION_S)
    ax.set_ylim(y_lo, y_hi)

    ext = [0, DURATION_S, y_lo, y_hi]
    ax.imshow(np.ma.masked_where(gb <= 0, gb), cmap=cmap_bid, norm=norm,
              extent=ext, aspect="auto", interpolation="bilinear")
    ax.imshow(np.ma.masked_where(ga <= 0, ga), cmap=cmap_ask, norm=norm,
              extent=ext, aspect="auto", interpolation="bilinear")

    # mid-price: layered glow
    ax.plot(mids_t, mids_p, color="#ffd76a", lw=7, alpha=0.14,
            solid_capstyle="round", zorder=5)
    ax.plot(mids_t, mids_p, color="#ffe89a", lw=3, alpha=0.35,
            solid_capstyle="round", zorder=6)
    ax.plot(mids_t, mids_p, color="#fffbe8", lw=1.1, alpha=0.95,
            solid_capstyle="round", zorder=7)

    # trades: glowing bubbles (whale prints pop)
    for pts, col in ((buy_pts, "#3ef2a1"), (sell_pts, "#ff5b5b")):
        if pts:
            xs_, ys_, vs_ = zip(*pts)
            sz = np.clip(14 + np.array(vs_) * 1.6, 14, 340)
            ax.scatter(xs_, ys_, s=sz * 2.4, c=col, alpha=0.07,
                       edgecolors="none", zorder=8)
            ax.scatter(xs_, ys_, s=sz, c=col, alpha=0.8,
                       edgecolors="#04060b", linewidths=0.4, zorder=9)

    # axes cosmetics: invisible spines, dim ticks, faint grid
    for sp in ax.spines.values():
        sp.set_visible(False)
    ax.tick_params(length=0, labelsize=8, colors="#3d4a5c")
    ax.set_xticks(np.arange(0, DURATION_S + 1, 10))
    ax.set_yticks(np.arange(round(pmin), pmax, 1.0))
    ax.grid(True, color="#16202e", lw=0.5, alpha=0.30, zorder=1)
    ax.set_axisbelow(True)

    # watermark baked into the art (not a title bar)
    ax.text(0.012, 0.965, "TMAP", transform=ax.transAxes, fontsize=15,
            fontweight="bold", color="#7fd8ff", alpha=0.55, family="monospace")
    ax.text(0.012, 0.928, "XAUUSD · L2 RECONSTRUCTED · SIM",
            transform=ax.transAxes, fontsize=7.5, color="#4a5a72",
            alpha=0.8, family="monospace")

    fig.savefig(OUT, facecolor=fig.get_facecolor())
    print(f"saved {OUT}: {len(frames)} frames, {len(tiles)} tiles, "
          f"vmax={vmax:.1f}, price {pmin:.2f}-{pmax:.2f}")


if __name__ == "__main__":
    main()
