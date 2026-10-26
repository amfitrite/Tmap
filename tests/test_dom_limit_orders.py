"""Tests for Bookmap-style limit-order views exposed by DOMCache."""
import unittest

from tmap.core.dom_cache import DOMCache, DOMCacheConfig
from tmap.core.types import Tick, TradeSide


def _synthetic_cache() -> DOMCache:
    dc = DOMCache(DOMCacheConfig(tick_size=0.01, half_life_s=600.0))
    ts = 1_700_000_000.0
    # Repeated seller touches at the bid -> inferred resting bid wall
    for i in range(20):
        dc.process_tick(Tick(ts + i * 0.05, 2749.99, 3.0, TradeSide.SELL,
                             bid=2749.98, ask=2750.02))
    # Aggressive sweep upward -> inferred resting ask liquidity consumed
    dc.process_tick(Tick(ts + 2.0, 2750.10, 30.0, TradeSide.BUY,
                         bid=2750.00, ask=2750.05))
    return dc


class TestLimitOrderViews(unittest.TestCase):
    def setUp(self):
        self.dc = _synthetic_cache()

    def test_get_limit_orders_sorted_by_strength(self):
        orders = self.dc.get_limit_orders(min_volume=0.1)
        self.assertTrue(orders, "expected non-empty limit order list")
        vols = [l.volume for l in orders]
        self.assertEqual(vols, sorted(vols, reverse=True))
        top = orders[0]
        self.assertEqual(top.side, "bid")
        self.assertGreaterEqual(top.touches, 15)

    def test_min_volume_filters_dust(self):
        orders = self.dc.get_limit_orders(min_volume=1000.0)
        self.assertEqual(orders, [])

    def test_ladder_is_price_sorted_per_side(self):
        lad = self.dc.get_ladder(depth=10)
        bids, asks = lad["bids"], lad["asks"]
        self.assertTrue(bids and asks)
        self.assertEqual([l.price for l in bids],
                         sorted((l.price for l in bids), reverse=True))
        self.assertEqual([l.price for l in asks],
                         sorted(l.price for l in asks))
        self.assertTrue(all(l.side == "bid" for l in bids))
        self.assertTrue(all(l.side == "ask" for l in asks))

    def test_ladder_respects_depth_cap(self):
        lad = self.dc.get_ladder(depth=3)
        self.assertLessEqual(len(lad["bids"]), 3)
        self.assertLessEqual(len(lad["asks"]), 3)

    def test_total_resting_liquidity(self):
        agg = self.dc.total_resting_liquidity()
        self.assertGreater(agg["bid_volume"], 0.0)
        self.assertGreater(agg["ask_volume"], 0.0)
        self.assertAlmostEqual(agg["bid_imbalance"],
                               agg["bid_volume"] / (agg["bid_volume"] + agg["ask_volume"]))

    def test_empty_cache_returns_empty_views(self):
        fresh = DOMCache(DOMCacheConfig())
        self.assertEqual(fresh.get_limit_orders(), [])
        lad = fresh.get_ladder()
        self.assertEqual(lad["bids"], [])
        self.assertEqual(lad["asks"], [])
        agg = fresh.total_resting_liquidity()
        self.assertEqual(agg["bid_volume"], 0.0)
        self.assertEqual(agg["bid_imbalance"], 0.5)

    def test_clear_resets_views(self):
        self.dc.clear()
        self.assertEqual(self.dc.get_limit_orders(), [])


if __name__ == "__main__":
    unittest.main()
