"""
Preview generator: renders the Tmap liquidity heatmap to a PNG image.

Pipeline (identical to the live app, but headless & time-accelerated):
    synthetic ticks  ->  RecordingSession (DOMCache -> frames -> tiles on disk)
                    ->  TileStore.query_window (viewport query)
                    ->  matplotlib heatmap PNG

Usage:
    python preview_heatmap.py [duration_seconds]
"""
import random
import sys
import tempfile

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

from tmap.core.heatmap_store import TileStore
from tmap.core.types import Tick, TradeSide

DURATION_S = float(sys.argv[1]) if len(sys.argv) > 1 else 60.0
TICK_SIZE = 0.01
FRAME_INTERVAL = 0.5


def synth_ticks(t0: float, duration: float, n: int = 9000):
    """Geometric-Brownian-ish walk with Poisson trades + whale blocks."""
    rng = random.Random(42)
    price = 2750.0
    for i in range(n):
        ts = t0 + duration * i / n
        step = rng.choice([-3, -2, -1, -1, 0, 0, 1, 1, 2, 3]) * TICK_SIZE
        # occasional impulse move to create fresh liquidity walls
        if rng.random() < 0.01:
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


def main():
    root = tempfile.mkdtemp(prefix="tmap_preview_")
    store = TileStore(root=root, tile_seconds=10.0, frame_interval_s=FRAME_INTERVAL)
    session = store.open_session(symbol="XAUUSD", tick_size=TICK_SIZE)

    t0 = 1_760_000_000.0
    ticks = list(synth_ticks(t0, DURATION_S))
    for tk in ticks:
        session.ingest(tk)
    tiles = session.finish()
    print(f"Recorded {len(tiles)} tile(s) -> {root}")

    t_end = t0 + DURATION_S

    # Viewport: full recorded window, wide price range
    frames = store.query_window("XAUUSD", t0, t_end, price_lo=2700.0, price_hi=2800.0)
    frames = [f for f in frames if f.cells]
    if not frames:
        raise SystemExit("No frames captured — check pipeline.")

    idx_set = set()
    for f in frames:
        idx_set |= set(f.cells.keys())
    idxs = sorted(idx_set)
    col_of = {pi: j for j, pi in enumerate(idxs)}
    price_axis = np.array(idxs) * TICK_SIZE  # snap index -> price

    n_cols = len(frames)
    grid_b = np.zeros((len(idxs), n_cols))
    grid_a = np.zeros((len(idxs), n_cols))
    buy_map = {}
    sell_map = {}
    for j, f in enumerate(frames):
        for pi, c in f.cells.items():
            r = col_of[pi]
            grid_b[r, j] = c.bid
            grid_a[r, j] = c.ask
        for (pi, v, side) in f.trades:
            if pi in col_of:
                m = buy_map if side == 1 else sell_map
                key = (col_of[pi], j)
                m[key] = m.get(key, 0.0) + v

    times = np.array([f.timestamp - t0 for f in frames])

    fig, ax = plt.subplots(figsize=(14, 7), dpi=110)
    fig.patch.set_facecolor("#0d1117")
    ax.set_facecolor("#0d1117")

    vmax_b = max(grid_b.max(), 1e-9)
    vmax_a = max(grid_a.max(), 1e-9)

    # Bid liquidity: green intensity; Ask: red intensity (TradingView-style)
    img_b = np.ma.masked_where(grid_b <= 0, grid_b)
    img_a = np.ma.masked_where(grid_a <= 0, grid_a)
    ax.imshow(img_b, aspect="auto", origin="lower", cmap="Greens",
              vmin=0, vmax=vmax_b, extent=[0, n_cols, price_axis.min(), price_axis.max()])
    ax.imshow(img_a, aspect="auto", origin="lower", cmap="Reds",
              vmin=0, vmax=vmax_a, extent=[0, n_cols, price_axis.min(), price_axis.max()])

    # Trade markers
    for (r, j), v in buy_map.items():
        ax.scatter(j, price_axis[r], s=min(60, 4 + v * 0.8), c="#00e676",
                   marker="^", alpha=0.85, linewidths=0)
    for (r, j), v in sell_map.items():
        ax.scatter(j, price_axis[r], s=min(60, 4 + v * 0.8), c="#ff1744",
                   marker="v", alpha=0.85, linewidths=0)

    # Mid-price line
    mids = np.array([f.mid_price for f in frames])
    ax.plot(np.arange(n_cols), mids, color="#ffd54f", lw=1.2, alpha=0.9, label="Mid")

    xticks = np.linspace(0, n_cols - 1, 9).astype(int)
    ax.set_xticks(xticks)
    ax.set_xticklabels([f"{times[i]:.0f}s" for i in xticks], color="#c9d1d9")
    ax.tick_params(colors="#c9d1d9")
    ax.set_xlabel("Waktu (detik sejak awal rekaman)", color="#c9d1d9")
    ax.set_ylabel("Harga (XAUUSD)", color="#c9d1d9")
    ax.set_title(f"Tmap Liquidity Heatmap — XAUUSD | {len(frames)} frames, "
                 f"{len(tiles)} tiles, durasi {DURATION_S:.0f}s",
                 color="#f0f6fc")
    ax.grid(False)
    ax.legend(loc="upper left", facecolor="#161b22", labelcolor="#c9d1d9")

    fig.tight_layout()
    out = "/workspace/heatmap_preview.png"
    fig.savefig(out, facecolor=fig.get_facecolor())
    print(f"Saved preview -> {out}")


if __name__ == "__main__":
    main()
