import unittest
import time
from tmap.core.types import MarketDepth, Tick
from tmap.network.mock_feed import MockMarketFeed


class TestMarketFeed(unittest.TestCase):
    def test_mock_feed_streaming(self):
        feed = MockMarketFeed(
            symbol="XAUUSD",
            initial_price=2750.0,
            tick_size=0.01,
            tick_interval_ms=10,
        )

        ticks_received = []
        depth_received = []

        feed.on_tick = lambda t: ticks_received.append(t)
        feed.on_depth = lambda d: depth_received.append(d)

        feed.start()
        self.assertTrue(feed.is_running)

        # Allow feed to generate data for 150 ms
        time.sleep(0.15)
        feed.stop()

        self.assertFalse(feed.is_running)
        self.assertGreater(len(ticks_received), 3)
        self.assertIsInstance(ticks_received[0], Tick)
        self.assertGreater(ticks_received[0].price, 0)
        self.assertGreater(ticks_received[0].volume, 0)


if __name__ == "__main__":
    unittest.main()
