"""Unit tests for the synthetic L2 DOM cache rebuilt from MT5 ticks."""
import tempfile
import unittest

from tmap.core.dom_cache import DOMCache, DOMCacheConfig
from tmap.core.types import MarketDepth, Tick, TradeSide


def mk_tick(ts, price, vol, side, bid=None, ask=None):
    return Tick(
        timestamp=ts,
        price=price,
        volume=vol,
        side=side,
        bid=bid if bid is not None else price - 0.01,
        ask=ask if ask is not None else price + 0.01,
    )


class TestDOMCache(unittest.TestCase):
    def setUp(self):
        self.cfg = DOMCacheConfig(tick_size=0.01, half_life_s=60.0)
        self.cache = DOMCache(self.cfg)

    def test_touch_credits_opposite_side(self):
        """A BUY at the ask should leave inferred resting liquidity on the ask side."""
        self.cache.process_tick(mk_tick(1.0, 100.01, 10.0, TradeSide.BUY, bid=100.00, ask=100.01))
        depth = self.cache.get_depth()
        # First tick only sets state (no prev price), second tick generates credit
        self.cache.process_tick(mk_tick(2.0, 100.01, 10.0, TradeSide.BUY, bid=100.00, ask=100.01))
        depth = self.cache.get_depth()
        self.assertIn(100.01, depth.asks)
        self.assertGreater(depth.asks[100.01], 0.0)

    def test_sweep_allocates_across_levels(self):
        """A large upward sweep must distribute inferred liquidity across consumed ask levels."""
        self.cache.process_tick(mk_tick(1.0, 100.00, 5.0, TradeSide.SELL, bid=99.99, ask=100.00))
        # Sweep up 5 levels with heavy volume
        self.cache.process_tick(mk_tick(2.0, 100.05, 50.0, TradeSide.BUY, bid=100.00, ask=100.05))
        depth = self.cache.get_depth()
        swept_asks = [p for p in depth.asks if 100.00 <= p <= 100.05]
        self.assertGreaterEqual(len(swept_asks), 4, "sweep should mark multiple consumed levels")
        total = sum(v for p, v in depth.asks.items() if p <= 100.05)
        self.assertGreater(total, 20.0)

    def test_downward_sweep_hits_bids(self):
        self.cache.process_tick(mk_tick(1.0, 100.00, 5.0, TradeSide.BUY, bid=99.99, ask=100.00))
        self.cache.process_tick(mk_tick(2.0, 99.95, 40.0, TradeSide.SELL, bid=99.95, ask=99.96))
        depth = self.cache.get_depth()
        swept_bids = [p for p in depth.bids if 99.95 <= p <= 100.00]
        self.assertGreaterEqual(len(swept_bids), 4)

    def test_time_decay_fades_idle_walls(self):
        self.cache.process_tick(mk_tick(1.0, 100.00, 5.0, TradeSide.BUY, bid=99.99, ask=100.00))
        self.cache.process_tick(mk_tick(2.0, 100.00, 100.0, TradeSide.BUY, bid=99.99, ask=100.00))
        before = self.cache.get_depth().asks.get(100.00, 0.0)
        self.assertGreater(before, 0.0)
        # Advance two half-lives without new touches
        self.cache.apply_time_decay(now=2.0 + 2 * self.cfg.half_life_s)
        after = self.cache.get_depth().asks.get(100.00, 0.0)
        self.assertLess(after, before * 0.3, "wall should decay to <30% after 2 half-lives")

    def test_true_depth_overrides_estimates(self):
        depth = MarketDepth(timestamp=5.0, bids={99.98: 250.0}, asks={100.02: 300.0})
        self.cache.process_depth(depth)
        out = self.cache.get_depth()
        self.assertAlmostEqual(out.bids[99.98], 250.0)
        self.assertAlmostEqual(out.asks[100.02], 300.0)

    def test_memory_bound(self):
        cfg = DOMCacheConfig(tick_size=0.01, max_levels_per_side=10, depth_range_ticks=1000)
        cache = DOMCache(cfg)
        price = 100.0
        for i in range(200):
            side = TradeSide.BUY if i % 2 == 0 else TradeSide.SELL
            cache.process_tick(mk_tick(float(i), price + i * 0.01, 10.0, side))
        self.assertLessEqual(len(cache._bids), cfg.max_levels_per_side)
        self.assertLessEqual(len(cache._asks), cfg.max_levels_per_side)

    def test_heatmap_rows_shape(self):
        self.cache.process_tick(mk_tick(1.0, 100.00, 5.0, TradeSide.BUY))
        self.cache.process_tick(mk_tick(2.0, 100.03, 30.0, TradeSide.BUY))
        rows = self.cache.get_heatmap_rows()
        for price, bid_strength, ask_strength in rows:
            self.assertGreaterEqual(bid_strength, 0.0)
            self.assertGreaterEqual(ask_strength, 0.0)

    def test_largest_levels_reports_walls(self):
        self.cache.process_tick(mk_tick(1.0, 100.00, 1.0, TradeSide.BUY, bid=99.99, ask=100.00))
        self.cache.process_tick(mk_tick(2.0, 100.00, 500.0, TradeSide.SELL, bid=99.99, ask=100.00))
        walls = self.cache.largest_levels(n=3)
        self.assertTrue(walls["bid_walls"])
        top_price, top_vol = walls["bid_walls"][0]
        self.assertGreater(top_vol, 0.0)

    def test_clear_resets_state(self):
        self.cache.process_tick(mk_tick(1.0, 100.0, 5.0, TradeSide.BUY))
        self.cache.clear()
        self.assertEqual(self.cache.ticks_processed, 0)
        self.assertEqual(self.cache.get_depth().bids, {})
        self.assertEqual(self.cache.get_depth().asks, {})

    def test_engine_integration(self):
        from tmap.core.config import EngineConfig
        from tmap.core.engine import OrderFlowEngine

        engine = OrderFlowEngine(EngineConfig(tick_size=0.01, ticks_per_bar=1000,
                                              symbol="TESTDOM", cache_dir=tempfile.mkdtemp()))
        engine.process_tick(mk_tick(1.0, 100.00, 5.0, TradeSide.BUY, bid=99.99, ask=100.00))
        engine.process_tick(mk_tick(2.0, 100.04, 60.0, TradeSide.BUY, bid=100.00, ask=100.04))
        snap = engine.get_snapshot()
        self.assertGreater(engine.dom_cache.ticks_processed, 0)
        # Synthetic book should now provide a best bid/ask even without true depth feed
        self.assertGreater(snap.best_bid, 0.0)
        self.assertGreater(snap.best_ask, 0.0)


if __name__ == "__main__":
    unittest.main()
