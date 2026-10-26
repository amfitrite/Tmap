import os
import tempfile
import unittest
from tmap.core.dom_cache import DOMCache, DOMCacheConfig
from tmap.core.types import MarketDepth, Tick, TradeSide


class TestDOMCachePersistence(unittest.TestCase):
    def test_save_and_load_cache(self):
        cache1 = DOMCache(DOMCacheConfig(tick_size=0.01))
        # Feed ticks and depth
        cache1.process_tick(Tick(timestamp=1700000000.0, price=2750.50, volume=5.0, side=TradeSide.BUY, bid=2750.40, ask=2750.50))
        cache1.process_depth(MarketDepth(timestamp=1700000001.0, bids={2750.40: 10.0, 2750.30: 25.0}, asks={2750.60: 12.0, 2750.70: 30.0}))

        self.assertGreater(len(cache1.get_ladder()["bids"]), 0)
        self.assertGreater(len(cache1.get_ladder()["asks"]), 0)

        with tempfile.TemporaryDirectory() as tmpdir:
            file_path = os.path.join(tmpdir, "dom_cache.json")
            cache1.save_to_file(file_path)
            self.assertTrue(os.path.exists(file_path))

            # Create brand new clean cache instance and load from file
            cache2 = DOMCache(DOMCacheConfig(tick_size=0.01))
            self.assertEqual(len(cache2.get_ladder()["bids"]), 0)

            loaded = cache2.load_from_file(file_path)
            self.assertTrue(loaded)
            self.assertEqual(cache2.last_price, cache1.last_price)
            self.assertEqual(cache2.last_bid, cache1.last_bid)
            self.assertEqual(cache2.last_ask, cache1.last_ask)

            ladder2 = cache2.get_ladder()
            self.assertGreater(len(ladder2["bids"]), 0)
            self.assertGreater(len(ladder2["asks"]), 0)
            self.assertEqual(ladder2["bids"][0].price, 2750.40)


if __name__ == "__main__":
    unittest.main()
