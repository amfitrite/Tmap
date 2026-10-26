"""Tests for timeframe aggregation + chart display settings (right-click menu core logic)."""

import math

import pytest

from tmap.core.types import Tick, TradeSide, VolumeBar
from tmap.ui.components.base_canvas import ChartCanvas, aggregate_bars


def _bar(start: float, dur: float, o: float, h: float, l: float, c: float,
         vol: float = 10.0, idx: int = 0) -> VolumeBar:
    b = VolumeBar(index=idx, start_time=start, end_time=start + dur,
                  open=o, high=h, low=l, close=c)
    b.total_volume = vol
    b.buy_volume = vol / 2
    b.sell_volume = vol / 2
    b.delta = 0.0
    b.tick_count = 5
    b.is_sealed = True
    return b


class TestAggregateBars:
    def test_passthrough_for_native(self):
        bars = [_bar(0.0, 1.0, 10, 11, 9, 10.5)]
        assert aggregate_bars(bars, 0) == bars

    def test_groups_by_wall_clock_bucket(self):
        # Three 1s bars spanning two 5-second buckets [0..5) and [5..10)
        bars = [
            _bar(0.0, 1.0, 10.0, 10.5, 9.8, 10.2),
            _bar(1.0, 1.0, 10.2, 11.0, 10.0, 10.8),
            _bar(5.0, 1.0, 10.8, 12.0, 10.6, 11.5),
        ]
        out = aggregate_bars(bars, 5)
        assert len(out) == 2
        first, second = out
        # OHLCV merge rules
        assert first.open == 10.0 and first.close == 10.8
        assert first.high == 11.0 and first.low == 9.8
        assert first.total_volume == 20.0 and first.tick_count == 10
        assert math.isclose(first.delta, 0.0)
        assert second.open == 10.8 and second.high == 12.0
        # Bucket alignment: candle starts at the tf boundary, not first tick ts
        assert first.start_time == 0.0 and second.start_time == 5.0

    def test_empty_input(self):
        assert aggregate_bars([], 60) == []

    def test_single_bar_larger_tf(self):
        bars = [_bar(3.0, 1.0, 1, 2, 0.5, 1.5)]
        out = aggregate_bars(bars, 3600)
        assert len(out) == 1
        assert out[0].start_time == 0.0  # aligned to hour boundary


try:
    import PySide6  # noqa: F401  (availability probe for conditional tests)
    HAS_PYSIDE6 = True
except ImportError:
    HAS_PYSIDE6 = False


@pytest.mark.skipif(not HAS_PYSIDE6, reason="PySide6 not installed")
class TestCanvasDisplaySettings:
    """Headless-safe: only exercises non-painting logic of ChartCanvas."""

    def setup_method(self):
        from PySide6.QtWidgets import QApplication
        import sys
        self.app = QApplication.instance() or QApplication(sys.argv)
        self.canvas = ChartCanvas()

    def test_set_timeframe_and_cache_invalidation(self):
        self.canvas.bars = [_bar(float(i), 1.0, 10, 11, 9, 10.5) for i in range(10)]
        self.canvas.set_timeframe(5)
        assert self.canvas.tf_seconds == 5
        series = self.canvas._display_series()
        assert len(series) == 2  # ten 1s bars -> two 5s candles
        # Re-fetch hits cache (same key) and returns identical object list
        assert self.canvas._display_series() is series

    def test_native_mode_no_aggregation(self):
        self.canvas.bars = [_bar(float(i), 1.0, 10, 11, 9, 10.5) for i in range(4)]
        self.canvas.set_timeframe(0)
        assert len(self.canvas._display_series()) == 4

    def test_chart_type_validation(self):
        self.canvas.set_chart_type("Line")
        assert self.canvas.chart_type == "Line"
        self.canvas.set_chart_type("Bogus")
        assert self.canvas.chart_type == "Line"  # rejected

    def test_zoom_bounds(self):
        for _ in range(50):
            self.canvas.zoom_in()
        assert self.canvas.scale_x <= 60.0
        for _ in range(100):
            self.canvas.zoom_out()
        assert self.canvas.scale_x >= 2.0

    def test_live_bar_merged_into_bucket(self):
        self.canvas.bars = [_bar(0.0, 1.0, 10, 11, 9, 10.5)]
        live = _bar(1.0, 1.0, 10.5, 13.0, 9.0, 12.0)
        live.is_sealed = False
        self.canvas.current_bar = live
        self.canvas.set_timeframe(5)
        series = self.canvas._display_series()
        assert len(series) == 1
        assert series[0].high == 13.0 and series[0].close == 12.0

    def test_context_menu_constructs_with_all_timeframes(self):
        from tmap.ui.components.context_menu import ChartContextMenu, TIMEFRAMES
        picked = {}
        menu = ChartContextMenu(on_timeframe=lambda s: picked.update(tf=s))
        submenu_titles = [a.text() for a in menu.actions()]
        assert any("Candle Interval" in t for t in submenu_titles)
        # every advertised timeframe exists as an action inside the submenu
        tf_actions = [a for a in menu.findChildren(type(menu.actions()[0]))]
        labels = {a.text() for a in tf_actions if a.text() in {lbl for lbl, _ in TIMEFRAMES}}
        assert labels == {lbl for lbl, _ in TIMEFRAMES}
        assert {"1s", "4h"} <= labels
