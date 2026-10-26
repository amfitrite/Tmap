import os
import tempfile
import unittest

from tmap.core.config import EngineConfig
from tmap.core.engine import OrderFlowEngine
from tmap.core.types import Tick, TradeSide


def _feed_ticks(engine: OrderFlowEngine, n: int, start_ts: float = 1_700_000_000.0) -> None:
    price = 2750.0
    for i in range(n):
        engine.process_tick(Tick(
            timestamp=start_ts + i * 0.05,
            price=price + (i % 7) * 0.05,
            volume=2.0,
            side=TradeSide.BUY if i % 2 == 0 else TradeSide.SELL,
            bid=price - 0.05,
            ask=price,
        ))


class TestCandlePersistence(unittest.TestCase):
    def test_candles_saved_and_restored_across_sessions(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cfg = EngineConfig(symbol="TEST", ticks_per_bar=10, cache_dir=tmpdir)
            eng1 = OrderFlowEngine(config=cfg)
            _feed_ticks(eng1, 35)  # seals at least 3 bars
            self.assertGreater(len(eng1.bars), 0)
            eng1.save_cache()

            self.assertTrue(os.path.exists(eng1.candles_file))

            # Brand-new engine pointing at the same cache dir must reload candles
            eng2 = OrderFlowEngine(config=EngineConfig(symbol="TEST", ticks_per_bar=10, cache_dir=tmpdir))
            self.assertEqual(len(eng2.bars), len(eng1.bars))
            b1, b2 = eng1.bars[-1], eng2.bars[-1]
            self.assertEqual(b1.open, b2.open)
            self.assertEqual(b1.close, b2.close)
            self.assertEqual(b1.total_volume, b2.total_volume)
            self.assertTrue(b2.is_sealed)
            # Bar index continues after restored history (no duplicate indices)
            idx_before = eng2._bar_index
            self.assertGreaterEqual(idx_before, b2.index)

    def test_restored_engine_continues_new_bars_without_index_collision(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cfg = EngineConfig(symbol="TEST", ticks_per_bar=10, cache_dir=tmpdir)
            eng1 = OrderFlowEngine(config=cfg)
            _feed_ticks(eng1, 20)
            eng1.save_cache()

            eng2 = OrderFlowEngine(config=EngineConfig(symbol="TEST", ticks_per_bar=10, cache_dir=tmpdir))
            old_indices = {b.index for b in eng2.bars}
            _feed_ticks(eng2, 30, start_ts=1_700_000_100.0)
            new_indices = {b.index for b in eng2.bars} - old_indices
            self.assertTrue(new_indices)
            self.assertFalse(old_indices & new_indices)

    def test_corrupt_candle_file_is_ignored(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cfg = EngineConfig(symbol="TEST", cache_dir=tmpdir)
            eng1 = OrderFlowEngine(config=cfg)
            with open(eng1.candles_file, "w") as f:
                f.write("{not valid json!![")
            eng2 = OrderFlowEngine(config=EngineConfig(symbol="TEST", cache_dir=tmpdir))
            self.assertEqual(len(eng2.bars), 0)

    def test_persist_candles_disabled_skips_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cfg = EngineConfig(symbol="TEST", ticks_per_bar=10, cache_dir=tmpdir, persist_candles=False)
            eng = OrderFlowEngine(config=cfg)
            _feed_ticks(eng, 15)
            eng.save_cache()
            self.assertFalse(os.path.exists(eng.candles_file))
            self.assertTrue(os.path.exists(eng.cache_file))  # DOM still saved


if __name__ == "__main__":
    unittest.main()
