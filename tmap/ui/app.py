import sys
from typing import Optional

try:
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication, QMainWindow, QVBoxLayout, QWidget
    HAS_PYSIDE = True
except ImportError:
    HAS_PYSIDE = False
    QMainWindow = object

from tmap.core.config import EngineConfig
from tmap.core.engine import OrderFlowEngine
from tmap.core.events import EventType
from tmap.network.mock_feed import MockMarketFeed
from tmap.ui.components.base_canvas import ChartCanvas
from tmap.ui.components.control_panel import ControlPanel
from tmap.ui.components.status_bar import TerminalStatusBar
from tmap.ui.theme import QSS_STYLESHEET
from tmap.utils.logger import get_logger

logger = get_logger("App")


class TmapMainWindow(QMainWindow if HAS_PYSIDE else object):
    """
    Main application window hosting controls, orderflow chart canvas, and metrics.
    Serves as the reference container for custom designer layouts.
    """

    def __init__(self, engine: OrderFlowEngine, feed: Optional[MockMarketFeed] = None) -> None:
        if not HAS_PYSIDE:
            raise RuntimeError("PySide6 is required to run the Tmap graphical application.")

        super().__init__()
        self.engine = engine
        self.feed = feed or MockMarketFeed(symbol=self.engine.config.symbol)

        self.setWindowTitle(f"Tmap — [{self.engine.config.symbol}] Order Flow Terminal")
        self.resize(1280, 800)
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

        # 1. Top control toolbar
        self.control_panel = ControlPanel(
            on_toggle_feed=self._toggle_feed,
            on_reset_view=self._reset_camera,
            parent=self,
        )
        layout.addWidget(self.control_panel)

        # 2. Main chart canvas
        self.canvas = ChartCanvas(parent=self)
        layout.addWidget(self.canvas, stretch=1)

        # 3. Status bar
        self.status_bar = TerminalStatusBar(parent=self)
        self.setStatusBar(self.status_bar)

    def _wire_events(self) -> None:
        # Route engine events to canvas
        self.engine.event_bus.subscribe(EventType.TICK_RECEIVED, self.canvas.on_tick)
        self.engine.event_bus.subscribe(EventType.BAR_UPDATED, self.canvas.on_bar_updated)
        self.engine.event_bus.subscribe(EventType.BAR_SEALED, self.canvas.on_bar_sealed)
        self.engine.event_bus.subscribe(EventType.DEPTH_UPDATED, self.canvas.on_depth_updated)

        # Wire feed callbacks to engine
        self.feed.on_tick = self.engine.process_tick
        self.feed.on_depth = self.engine.process_depth

    def _toggle_feed(self) -> None:
        if self.feed.is_running:
            self.feed.stop()
            self.control_panel.btn_feed.setText("▶ Resume Feed")
            self.status_bar.lbl_status.setText("⏸ Feed Paused")
        else:
            self.feed.start()
            self.control_panel.btn_feed.setText("⏸ Pause Feed")
            self.status_bar.lbl_status.setText("🟢 Live Connected")

    def _reset_camera(self) -> None:
        self.canvas.reset_view()

    def _on_metrics_tick(self) -> None:
        snapshot = self.engine.get_snapshot()
        self.control_panel.update_snapshot(snapshot)
        self.status_bar.update_ticks(snapshot.total_ticks_processed)

    def closeEvent(self, event) -> None:
        if self.feed and self.feed.is_running:
            self.feed.stop()
        event.accept()


def main() -> None:
    """Entry point for standalone execution."""
    if not HAS_PYSIDE:
        print("Error: PySide6 is not installed. Please install it with 'pip install PySide6'.")
        sys.exit(1)

    app = QApplication(sys.argv)
    app.setStyle("Fusion")

    config = EngineConfig(symbol="XAUUSD", ticks_per_bar=50)
    engine = OrderFlowEngine(config=config)
    feed = MockMarketFeed(symbol=config.symbol, initial_price=2750.0, tick_interval_ms=20)

    window = TmapMainWindow(engine=engine, feed=feed)
    window.show()

    # Start feed after showing window
    feed.start()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
