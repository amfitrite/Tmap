import unittest
import tempfile
import time
from tmap.core.config import EngineConfig
from tmap.core.engine import OrderFlowEngine
from tmap.core.events import EventType
from tmap.core.types import MarketDepth, Tick, TradeSide


class TestOrderFlowEngine(unittest.TestCase):
    def setUp(self):
        # Isolated cache dir so persisted candles/DOM from other sessions or
        # tests never leak into engine state (keeps assertions deterministic).
        self._cache_tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._cache_tmp.cleanup)
        self.config = EngineConfig(
            symbol="XAUUSD",
            tick_size=0.01,
            ticks_per_bar=5,
            max_buffer_bars=10,
            max_recent_ticks=20,
            cache_dir=self._cache_tmp.name,
        )
        self.engine = OrderFlowEngine(config=self.config)

    def test_single_tick_processing(self):
        tick = Tick(
            timestamp=time.time(),
            price=2750.50,
            volume=2.5,
            side=TradeSide.BUY,
            bid=2750.40,
            ask=2750.50,
        )
        self.engine.process_tick(tick)

        self.assertEqual(self.engine.total_ticks, 1)
        self.assertEqual(self.engine.cumulative_volume_delta, 2.5)
        self.assertIsNotNone(self.engine.current_bar)
        self.assertEqual(self.engine.current_bar.open, 2750.50)
        self.assertEqual(self.engine.current_bar.high, 2750.50)
        self.assertEqual(self.engine.current_bar.low, 2750.50)
        self.assertEqual(self.engine.current_bar.close, 2750.50)
        self.assertEqual(self.engine.current_bar.total_volume, 2.5)
        self.assertEqual(self.engine.current_bar.buy_volume, 2.5)
        self.assertEqual(self.engine.current_bar.sell_volume, 0.0)

    def test_bar_sealing_after_ticks_per_bar(self):
        sealed_bars = []
        self.engine.event_bus.subscribe(
            EventType.BAR_SEALED, lambda b: sealed_bars.append(b)
        )

        for i in range(12):
            side = TradeSide.BUY if i % 2 == 0 else TradeSide.SELL
            tick = Tick(
                timestamp=time.time() + i,
                price=2750.0 + i * 0.1,
                volume=1.0,
                side=side,
            )
            self.engine.process_tick(tick)

        # ticks_per_bar = 5, 12 ticks should seal 2 bars (at tick 5 and 10), 2 ticks in active bar
        self.assertEqual(len(sealed_bars), 2)
        self.assertEqual(len(self.engine.bars), 2)
        self.assertTrue(sealed_bars[0].is_sealed)
        self.assertEqual(sealed_bars[0].tick_count, 5)
        self.assertEqual(self.engine.current_bar.tick_count, 2)

    def test_buffer_bounded_limits(self):
        # max_buffer_bars = 10, max_recent_ticks = 20
        # Process 70 ticks (14 bars sealed)
        for i in range(70):
            tick = Tick(
                timestamp=time.time() + i,
                price=2750.0 + (i % 5) * 0.1,
                volume=1.0,
                side=TradeSide.BUY,
            )
            self.engine.process_tick(tick)

        # Deque must be bounded and never exceed maxlen
        self.assertLessEqual(len(self.engine.bars), 10)
        self.assertLessEqual(len(self.engine.recent_ticks), 20)

    def test_market_depth_and_snapshot(self):
        depth = MarketDepth(
            timestamp=time.time(),
            bids={2749.50: 10.0, 2749.00: 25.0},
            asks={2750.50: 15.0, 2751.00: 30.0},
        )
        self.engine.process_depth(depth)

        snapshot = self.engine.get_snapshot()
        self.assertEqual(snapshot.symbol, "XAUUSD")
        self.assertEqual(snapshot.best_bid, 2749.50)
        self.assertEqual(snapshot.best_ask, 2750.50)


if __name__ == "__main__":
    unittest.main()
