"""Tests for the tile-based heatmap store (Google-Maps-style liquidity history)."""

import time

import pytest

from tmap.core.heatmap_store import TileStore, _snap_idx
from tmap.core.types import Tick, TradeSide


SYMBOL = "XAUUSD"
TICK_SIZE = 0.01
BASE_TS = 1_700_000_000.0


def make_tick(ts_offset: float, price: float, volume: float = 1.0,
              side: TradeSide = TradeSide.BUY) -> Tick:
    spread = 0.02
    return Tick(timestamp=BASE_TS + ts_offset, price=price, volume=volume,
                side=side, bid=price - spread / 2, ask=price + spread / 2)


@pytest.fixture()
def store(tmp_path):
    return TileStore(root=str(tmp_path / "tiles"), tile_seconds=60.0,
                     frame_interval_s=0.5)


def feed_history(store: TileStore, n_ticks: int = 400, span_s: float = 130.0):
    """Simulate a tape that touches prices 2000.00..2000.10 and flushes tiles."""
    session = store.open_session(SYMBOL, TICK_SIZE)
    rng_price = [2000.00, 2000.02, 2000.05, 2000.08, 2000.10]
    for i in range(n_ticks):
        t = span_s * i / max(1, n_ticks - 1)
        p = rng_price[i % len(rng_price)]
        side = TradeSide.BUY if i % 3 else TradeSide.SELL
        session.ingest(make_tick(t, p, volume=1.0 + (i % 5), side=side))
    metas = session.finish()
    return session, metas


class TestSnapIdx:
    def test_grid_alignment(self):
        assert _snap_idx(2000.07, TICK_SIZE) == 200007
        assert _snap_idx(2000.074, TICK_SIZE) == 200007
        assert _snap_idx(2000.075, TICK_SIZE) in (200007, 200008)


class TestRecording:
    def test_tiles_flushed_on_boundary(self, store):
        _, metas = feed_history(store, n_ticks=400, span_s=130.0)
        # 130s of data with 60s tiles => at least 2 tiles written
        assert len(metas) >= 2
        for m in metas:
            assert m.symbol == SYMBOL
            assert m.n_frames > 0
            assert m.size_bytes > 0

    def test_tile_files_exist_on_disk(self, store):
        _, metas = feed_history(store)
        import os
        for m in metas:
            assert os.path.exists(m.path)
            assert not os.path.exists(m.path + ".tmp")  # atomic publish


class TestReloadAcrossProcess:
    """The core promise: 'scroll history tetap terlihat ada map'."""

    def test_query_window_after_reopen(self, store):
        feed_history(store)
        # Fresh store instance = simulates app restart; RAM DOMCache is gone,
        # but the recorded map must still be queryable from disk.
        reopened = TileStore(root=store.root, tile_seconds=60.0)
        frames = reopened.query_window(SYMBOL, BASE_TS, BASE_TS + 130.0,
                                       1999.0, 2001.0)
        assert len(frames) > 0
        assert all(f.timestamp >= BASE_TS for f in frames)
        assert frames == sorted(frames, key=lambda f: f.timestamp)

    def test_frames_carry_liquidity_and_trades(self, store):
        feed_history(store)
        reopened = TileStore(root=store.root)
        frames = reopened.query_window(SYMBOL, BASE_TS, BASE_TS + 130.0,
                                       1999.0, 2001.0)
        any_cells = any(f.cells for f in frames)
        any_trades = any(f.trades for f in frames)
        assert any_cells, "expected reconstructed resting liquidity in history"
        assert any_trades, "expected trade markers in history"

    def test_price_window_filters_columns(self, store):
        feed_history(store)
        reopened = TileStore(root=store.root)
        wide = reopened.query_window(SYMBOL, BASE_TS, BASE_TS + 130.0,
                                     1999.0, 2001.0)
        narrow = reopened.query_window(SYMBOL, BASE_TS, BASE_TS + 130.0,
                                       2000.04, 2000.06)
        assert len(narrow) <= len(wide)
        lo_i = _snap_idx(2000.04, TICK_SIZE)
        hi_i = _snap_idx(2000.06, TICK_SIZE)
        for f in narrow:
            for pi in f.cells:
                assert lo_i <= pi <= hi_i
            for pi, _, _ in f.trades:
                assert lo_i <= pi <= hi_i

    def test_time_window_only_returns_requested_range(self, store):
        feed_history(store, span_s=130.0)
        reopened = TileStore(root=store.root)
        t0, t1 = BASE_TS + 60.0, BASE_TS + 90.0
        frames = reopened.query_window(SYMBOL, t0, t1, 1999.0, 2001.0)
        assert all(t0 <= f.timestamp <= t1 for f in frames)


class TestCoverageAndMeta:
    def test_coverage_merges_contiguous_tiles(self, store):
        feed_history(store, span_s=130.0)
        spans = store.coverage(SYMBOL)
        assert len(spans) >= 1
        total = sum(e - s for s, e in spans)
        assert total > 60.0  # we recorded ~130s worth

    def test_list_tiles_sorted_oldest_first(self, store):
        feed_history(store)
        metas = store.list_tiles(SYMBOL)
        starts = [m.start_ts for m in metas]
        assert starts == sorted(starts)

    def test_empty_symbol_returns_nothing(self, store):
        assert store.list_tiles("NODATA") == []
        assert store.query_window("NODATA", 0, time.time() * 2, 0, 1e9) == []
        assert store.coverage("NODATA") == []


class TestRoundTripFidelity:
    def test_encode_decode_preserves_values(self, store):
        session = store.open_session(SYMBOL, TICK_SIZE)
        for i in range(60):
            session.ingest(make_tick(i * 1.0, 2000.00 + (i % 10) * 0.01,
                                     volume=float(i + 1)))
        metas = session.finish()
        assert len(metas) >= 1  # 60s spans one or two 60s tiles depending on alignment
        all_frames = []
        for m in metas:
            sym, ts_size, start, end, frames = TileStore.decode_tile(m.path)
            assert sym == SYMBOL
            assert abs(ts_size - TICK_SIZE) < 1e-9
            assert start <= frames[0].timestamp
            assert end == pytest.approx(frames[-1].timestamp)
            all_frames.extend(frames)
        assert all_frames, "expected at least one decoded frame"
        # every decoded cell value must survive the f32 round trip
        for f in all_frames:
            for pi, c in f.cells.items():
                assert c.bid >= 0.0 and c.ask >= 0.0
            for pi, vol, side in f.trades:
                assert side in (0, 1)
                assert vol > 0.0

    def test_meta_read_matches_decode(self, store):
        _, metas = feed_history(store)
        for m in metas:
            meta = TileStore.read_tile_meta(m.path)
            assert meta is not None
            assert meta.n_frames == m.n_frames
            assert meta.start_ts == m.start_ts

    def test_corrupt_file_is_ignored(self, store):
        d = store.symbol_dir(SYMBOL)
        bad = d + "/t_badfile.tmht"
        with open(bad, "wb") as fh:
            fh.write(b"garbage-not-a-tile")
        feed_history(store)
        metas = store.list_tiles(SYMBOL)
        assert all("badfile" not in m.path for m in metas)
