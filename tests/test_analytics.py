import unittest
import time
from tmap.analytics.orderflow import calculate_delta_percent, calculate_imbalances
from tmap.analytics.rvol import calculate_rvol, classify_volume_intensity
from tmap.analytics.vbp import compute_volume_profile
from tmap.core.types import Tick, TradeSide, VolumeBar


class TestAnalytics(unittest.TestCase):
    def test_delta_percent(self):
        bar = VolumeBar(
            index=1,
            start_time=time.time(),
            end_time=time.time(),
            open=100.0,
            high=105.0,
            low=99.0,
            close=104.0,
            total_volume=100.0,
            buy_volume=75.0,
            sell_volume=25.0,
            delta=50.0,
        )
        delta_pct = calculate_delta_percent(bar)
        self.assertEqual(delta_pct, 50.0)

    def test_imbalances_detection(self):
        bar = VolumeBar(
            index=1,
            start_time=time.time(),
            end_time=time.time(),
            open=100.0,
            high=102.0,
            low=100.0,
            close=102.0,
            profile={
                100.0: [10.0, 2.0],  # buy, sell
                101.0: [40.0, 1.0],  # 40 buy vs 2 sell at lower = 20x imbalance!
                102.0: [5.0, 0.0],
            },
        )
        imbalances = calculate_imbalances(bar, ratio_threshold=3.0, min_volume=1.0)
        self.assertTrue(len(imbalances) > 0)
        buy_imb = [imb for imb in imbalances if imb.is_buy_imbalance]
        self.assertTrue(any(imb.price == 101.0 for imb in buy_imb))

    def test_rvol_and_intensity(self):
        history = [100.0, 100.0, 100.0, 100.0]
        rvol_normal = calculate_rvol(120.0, history)
        self.assertAlmostEqual(rvol_normal, 1.2, places=2)
        self.assertEqual(classify_volume_intensity(rvol_normal), "NORMAL")

        rvol_ultra = calculate_rvol(350.0, history)
        self.assertAlmostEqual(rvol_ultra, 3.5, places=2)
        self.assertEqual(classify_volume_intensity(rvol_ultra), "ULTRA_HIGH")

    def test_volume_profile_poc_vah_val(self):
        bar1 = VolumeBar(
            index=1,
            start_time=time.time(),
            end_time=time.time(),
            open=100.0,
            high=103.0,
            low=99.0,
            close=101.0,
            profile={
                99.0: [5.0, 5.0],    # 10
                100.0: [10.0, 10.0], # 20
                101.0: [50.0, 50.0], # 100 (POC)
                102.0: [15.0, 15.0], # 30
                103.0: [5.0, 5.0],   # 10
            },
        )
        vp = compute_volume_profile([bar1])
        self.assertIsNotNone(vp)
        self.assertEqual(vp.poc_price, 101.0)
        self.assertEqual(vp.poc_volume, 100.0)
        self.assertGreaterEqual(vp.vah_price, 101.0)
        self.assertLessEqual(vp.val_price, 101.0)


if __name__ == "__main__":
    unittest.main()
