import argparse
import os
import sys
from typing import Optional

try:
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import (
        QApplication,
        QHBoxLayout,
        QMainWindow,
        QVBoxLayout,
        QWidget,
    )
    HAS_PYSIDE = True
except ImportError:
    HAS_PYSIDE = False
    QMainWindow = object

from tmap.core.config import EngineConfig
from tmap.core.engine import OrderFlowEngine
from tmap.core.events import EventType
from tmap.network.base import BaseFeed
from tmap.network.mock_feed import MockMarketFeed
from tmap.network.mt5_feed import MT5TCPFeed
from tmap.ui.components.base_canvas import ChartCanvas
from tmap.ui.components.control_panel import ControlPanel
from tmap.ui.components.dom_panel import DOMLadderPanel
from tmap.ui.components.status_bar import TerminalStatusBar
from tmap.ui.theme import QSS_STYLESHEET
from tmap.utils.logger import get_logger

logger = get_logger("App")


class TmapMainWindow(QMainWindow if HAS_PYSIDE else object):
    """
    Main application window hosting controls, orderflow chart canvas, and metrics.
    Connects to live MT5 Bridge via TCP port 5555 or synthetic mock feeds.
    Auto-restores and persists L2 DOM liquidity cache across sessions.
    """

    def __init__(self, engine: OrderFlowEngine, feed: Optional[BaseFeed] = None) -> None:
        if not HAS_PYSIDE:
            raise RuntimeError("PySide6 is required to run the Tmap graphical application.")

        super().__init__()
        self.engine = engine
        self.feed = feed or MT5TCPFeed(host="127.0.0.1", port=5555)

        # Frameless premium look: empty title bar so the OS chrome shows no
        # branding (info lives in the in-app toolbar instead).
        self.setWindowTitle("")
        self.resize(1340, 840)
        self.setStyleSheet(QSS_STYLESHEET)

        self._setup_layout()
        self._wire_events()

        # UI refresh timer for metrics snapshot (30 FPS decoupled from tick stream)
        self.metrics_timer = QTimer(self)
        self.metrics_timer.timeout.connect(self._on_metrics_tick)
        self.metrics_timer.start(33)

    def _setup_layout(self) -> None:
        central_widget = QWidget(self)
        self.setCentralWidget(central_widget)

        layout = QVBoxLayout(central_widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        initial_source = "MT5 Bridge (5555)" if isinstance(self.feed, MT5TCPFeed) else "Synthetic Mock Feed"

        # 1. Top control toolbar
        self.control_panel = ControlPanel(
            on_toggle_feed=self._toggle_feed,
            on_reset_view=self._reset_camera,
            on_change_source=self._switch_feed,
            initial_source=initial_source,
            parent=self,
        )
        layout.addWidget(self.control_panel)

        # 2. Main chart canvas + Bookmap-style DOM ladder (limit orders) side by side
        chart_area = QWidget(self)
        chart_layout = QHBoxLayout(chart_area)
        chart_layout.setContentsMargins(0, 0, 0, 0)
        chart_layout.setSpacing(0)

        self.canvas = ChartCanvas(parent=chart_area)
        chart_layout.addWidget(self.canvas, stretch=1)

        # L2 limit-order ladder reconstructed from the DOM cache inside the engine
        self.dom_panel = DOMLadderPanel(dom_cache=self.engine.dom_cache, parent=chart_area)
        # Immediately display pre-loaded cached L2 DOM state
        snapshot = self.engine.get_snapshot()
        if snapshot.last_price > 0:
            self.dom_panel.set_last_price(snapshot.last_price)
        self.dom_panel.update_ladder()

        # Hydrate the chart with candles restored from the persistent cache so
        # history is visible immediately on startup (not only after new ticks).
        self.canvas.load_history(list(self.engine.bars), current_bar=self.engine.current_bar,
                                 last_price=snapshot.last_price)

        # Right-click premium settings: DOM liquidity decay preset updates the
        # live engine cache; timeframe changes refresh the title bar label.
        self.canvas.on_decay_changed = self._set_dom_halflife
        self.canvas.on_timeframe_changed = self._on_canvas_timeframe

        chart_layout.addWidget(self.dom_panel, stretch=0)

        layout.addWidget(chart_area, stretch=1)

        # 3. Status bar
        self.status_bar = TerminalStatusBar(parent=self)
        self.setStatusBar(self.status_bar)
        self._update_status_feed_label()

    def _wire_events(self) -> None:
        # Route engine events to canvas
        self.engine.event_bus.subscribe(EventType.TICK_RECEIVED, self.canvas.on_tick)
        self.engine.event_bus.subscribe(EventType.BAR_UPDATED, self.canvas.on_bar_updated)
        self.engine.event_bus.subscribe(EventType.BAR_SEALED, self.canvas.on_bar_sealed)
        self.engine.event_bus.subscribe(EventType.DEPTH_UPDATED, self.canvas.on_depth_updated)

        # Wire feed callbacks to engine
        self.feed.on_tick = self.engine.process_tick
        self.feed.on_depth = self.engine.process_depth

    def _update_status_feed_label(self) -> None:
        if isinstance(self.feed, MT5TCPFeed):
            self.status_bar.lbl_status.setText(f"🟢 MT5 Bridge Server (Port {self.feed.port}) Listening...")
        else:
            self.status_bar.lbl_status.setText("🟢 Synthetic Mock Feed Active")

    def _switch_feed(self, source_name: str) -> None:
        """Switch active market feed source on the fly."""
        logger.info(f"Switching feed source to: {source_name}")
        if self.feed and self.feed.is_running:
            self.feed.stop()

        if "MT5" in source_name:
            self.feed = MT5TCPFeed(host="127.0.0.1", port=5555)
        else:
            ref_price = self.engine.get_snapshot().last_price
            if ref_price <= 0:
                ref_price = 2750.0
            self.feed = MockMarketFeed(symbol=self.engine.config.symbol, initial_price=ref_price, tick_interval_ms=20)

        self.feed.on_tick = self.engine.process_tick
        self.feed.on_depth = self.engine.process_depth
        self.feed.start()

        self.control_panel.btn_feed.setText("⏸ Pause Feed")
        self._update_status_feed_label()

    def _toggle_feed(self) -> None:
        if self.feed.is_running:
            self.feed.stop()
            self.control_panel.btn_feed.setText("▶ Resume Feed")
            self.status_bar.lbl_status.setText("⏸ Feed Paused")
        else:
            self.feed.start()
            self.control_panel.btn_feed.setText("⏸ Pause Feed")
            self._update_status_feed_label()

    def _reset_camera(self) -> None:
        self.canvas.reset_view()

    def _set_dom_halflife(self, half_life_s: float) -> None:
        """Live-update the DOMCache liquidity decay half-life (right-click menu)."""
        try:
            with self.engine._lock:
                self.engine.dom_cache.config.half_life_s = max(0.0, float(half_life_s))
            logger.info(f"DOM liquidity decay half-life set to {half_life_s}s")
        except Exception as exc:  # pragma: no cover - defensive UI hook
            logger.warning(f"Failed to apply DOM decay preset: {exc}")

    def _on_canvas_timeframe(self, seconds: int) -> None:
        """Reflect the active timeframe in the in-app toolbar brand chip.

        The OS title bar is intentionally kept empty (premium frameless
        look), so TF/symbol info is surfaced inside the app instead.
        """
        label = f"{seconds}s" if seconds < 60 else (
            f"{seconds // 3600}h" if seconds % 3600 == 0 else f"{seconds // 60}m")
        base = "Native" if seconds <= 0 else label
        self.control_panel.set_brand(
            f"{self.engine.config.symbol}", tf=base)

    def _on_metrics_tick(self) -> None:
        snapshot = self.engine.get_snapshot()
        self.control_panel.update_snapshot(snapshot)
        self.status_bar.update_ticks(snapshot.total_ticks_processed)
        self.dom_panel.set_last_price(snapshot.last_price)

    def closeEvent(self, event) -> None:
        if self.feed and self.feed.is_running:
            self.feed.stop()
        self.engine.save_cache()
        event.accept()


def main() -> None:
    """Entry point for standalone execution."""
    if not HAS_PYSIDE:
        print("Error: PySide6 is not installed. Please install it with 'pip install PySide6'.")
        sys.exit(1)

    parser = argparse.ArgumentParser(description="Tmap Terminal Application")
    parser.add_argument("--mock", action="store_true", help="Start with synthetic mock feed")
    parser.add_argument("--port", type=int, default=5555, help="TCP port for MT5 Bridge (default 5555)")
    parser.add_argument("--host", type=str, default="127.0.0.1", help="Host IP for MT5 Bridge (default 127.0.0.1)")
    args, _ = parser.parse_known_args()

    app = QApplication(sys.argv)
    app.setStyle("Fusion")

    config = EngineConfig(symbol="XAUUSD", ticks_per_bar=50)
    engine = OrderFlowEngine(config=config)

    if args.mock:
        feed = MockMarketFeed(symbol=config.symbol, initial_price=2750.0, tick_interval_ms=20)
    else:
        # Default: connect to MT5 Bridge on port 5555
        feed = MT5TCPFeed(host=args.host, port=args.port)

    window = TmapMainWindow(engine=engine, feed=feed)
    window.show()

    # Start feed after showing window
    feed.start()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
