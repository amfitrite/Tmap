import math
from dataclasses import replace
from typing import Callable, Deque, List, Optional
from collections import deque

try:
    from PySide6.QtCore import Qt, QPointF, QRectF
    from PySide6.QtGui import QBrush, QColor, QFont, QPainter, QPaintEvent, QPen, QWheelEvent, QMouseEvent
    from PySide6.QtWidgets import QWidget
    HAS_PYSIDE = True
except ImportError:
    HAS_PYSIDE = False
    QWidget = object

from tmap.core.types import MarketDepth, Tick, VolumeBar
from tmap.ui.components.context_menu import CHART_TYPES, TIMEFRAMES
from tmap.ui.theme import THEME
from tmap.utils.math_helpers import clamp


def aggregate_bars(bars: List[VolumeBar], tf_seconds: int) -> List[VolumeBar]:
    """Aggregate base (tick-sealed) bars into higher-timeframe candles.

    Buckets are aligned to absolute epoch time (floor(ts / tf)), exactly like
    trading platforms, so a "5m" candle always spans a wall-clock 5-minute
    boundary regardless of when the session started. Reused VolumeBar fields:
    profile is intentionally dropped on aggregation (per-price footprint only
    makes sense at the native bar resolution).
    """
    if tf_seconds <= 0 or not bars:
        return list(bars)

    out: List[VolumeBar] = []
    cur_bucket: Optional[int] = None
    acc: Optional[VolumeBar] = None

    for b in bars:
        bucket = int(math.floor(b.start_time / tf_seconds))
        if cur_bucket is None or bucket != cur_bucket:
            if acc is not None:
                out.append(acc)
            # Align candle start to the wall-clock timeframe boundary
            acc = replace(b, index=len(out), start_time=bucket * tf_seconds,
                          end_time=b.end_time, is_sealed=True, profile={})
            cur_bucket = bucket
            continue
        assert acc is not None
        acc.high = max(acc.high, b.high)
        acc.low = min(acc.low, b.low)
        acc.close = b.close
        acc.end_time = b.end_time
        acc.total_volume += b.total_volume
        acc.buy_volume += b.buy_volume
        acc.sell_volume += b.sell_volume
        acc.delta += b.delta
        acc.tick_count += b.tick_count

    if acc is not None:
        out.append(acc)
    return out


class ChartCanvas(QWidget if HAS_PYSIDE else object):
    """
    Primary Order Flow & Microstructure Visual Canvas.
    Supports high-refresh rendering, dynamic scaling, interactive pan/zoom,
    right-click premium settings menu (timeframes 1s..4h, chart type, overlays).
    Adheres to IChartView protocol contract.
    """

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        if HAS_PYSIDE:
            super().__init__(parent)
            self.setMouseTracking(True)
            self.setAttribute(Qt.WA_OpaquePaintEvent, True)

        # Visual state
        self.bars: List[VolumeBar] = []
        self.current_bar: Optional[VolumeBar] = None
        self.last_depth: Optional[MarketDepth] = None
        self.current_price: float = 0.0

        # Camera & Transform
        self.scale_x: float = 12.0      # Pixels per bar
        self.scale_y: float = 8.0       # Pixels per price unit
        self.offset_x: float = 0.0
        self.offset_y: float = 0.0
        self.is_dragging: bool = False
        self.last_mouse_pos = None

        # ---- Display settings (driven by the right-click context menu) ----
        self.tf_seconds: int = 0                    # 0 = native tick/volume bars
        self.chart_type: str = "Candles"            # Candles | Bars (OHLC) | Line
        self.grid_visible: bool = True
        self.price_line_visible: bool = True
        # Callback invoked with the chosen timeframe seconds whenever the
        # user picks one from the Candle Interval submenu (wired by the app).
        self.on_timeframe_changed: Optional[Callable[[int], None]] = None
        # App-level hook for the "Load Limit-Order History" menu action,
        # invoked with the requested lookback in hours (negative = force
        # fresh MT5 download). Wired by TmapMainWindow.
        self.on_load_history: Optional[Callable[[float], None]] = None
        self._menu = None                           # lazily built ChartContextMenu
        self._display_cache_key: Optional[tuple] = None
        self._display_bars: List[VolumeBar] = []

    def on_tick(self, tick: Tick) -> None:
        self.current_price = tick.price
        if HAS_PYSIDE:
            self.update()

    def on_bar_updated(self, bar: VolumeBar) -> None:
        self.current_bar = bar
        if HAS_PYSIDE:
            self.update()

    def on_bar_sealed(self, bar: VolumeBar) -> None:
        self.bars.append(bar)
        if len(self.bars) > 1000:
            self.bars.pop(0)
        if HAS_PYSIDE:
            self.update()

    def on_depth_updated(self, depth: MarketDepth) -> None:
        self.last_depth = depth
        if HAS_PYSIDE:
            self.update()

    def load_history(self, bars: List[VolumeBar], current_bar: Optional[VolumeBar] = None,
                     last_price: float = 0.0) -> None:
        """Hydrate the chart with previously cached candles so history survives restarts."""
        self.bars = list(bars)[-1000:]
        if current_bar is not None and current_bar.tick_count > 0:
            self.current_bar = current_bar
        if last_price > 0:
            self.current_price = last_price
        if HAS_PYSIDE:
            self.update()

    def reset_view(self) -> None:
        self.offset_x = 0.0
        self.offset_y = 0.0
        if HAS_PYSIDE:
            self.update()

    # ------------------------------------------------------------------
    # Display settings & right-click context menu
    # ------------------------------------------------------------------
    def set_timeframe(self, seconds: int) -> None:
        """Switch candle aggregation interval (0 = native bars)."""
        self.tf_seconds = max(0, int(seconds))
        self._display_cache_key = None
        if HAS_PYSIDE:
            self.update()

    def set_chart_type(self, name: str) -> None:
        if name in CHART_TYPES:
            self.chart_type = name
            if HAS_PYSIDE:
                self.update()

    def zoom_in(self) -> None:
        self.scale_x = clamp(self.scale_x * 1.25, 2.0, 60.0)
        if HAS_PYSIDE:
            self.update()

    def zoom_out(self) -> None:
        self.scale_x = clamp(self.scale_x * 0.8, 2.0, 60.0)
        if HAS_PYSIDE:
            self.update()

    def _display_series(self) -> List[VolumeBar]:
        """Bars as currently displayed (aggregated to the chosen timeframe),
        with the live forming bar merged into its bucket."""
        sealed = list(self.bars)
        live = self.current_bar
        key = (self.tf_seconds, len(sealed),
               live.end_time if live else 0.0,
               live.close if live else 0.0)
        if self._display_cache_key == key:
            return self._display_bars

        if self.tf_seconds > 0 and live is not None and live.tick_count > 0:
            series = aggregate_bars(sealed + [live], self.tf_seconds)
        else:
            series = aggregate_bars(sealed, self.tf_seconds) if self.tf_seconds > 0 else sealed
        self._display_cache_key = key
        self._display_bars = series
        return series

    def _build_menu(self):
        from tmap.ui.components.context_menu import ChartContextMenu
        return ChartContextMenu(
            parent=self,
            current_tf_seconds=self.tf_seconds,
            chart_type=self.chart_type,
            grid_visible=self.grid_visible,
            price_line_visible=self.price_line_visible,
            decay_halflife=getattr(self, "dom_half_life_ref", 60.0),
            on_timeframe=self._on_menu_timeframe,
            on_chart_type=self.set_chart_type,
            on_toggle_grid=self._on_menu_grid,
            on_toggle_price_line=self._on_menu_price_line,
            on_decay_halflife_changed=self._on_menu_decay,
            on_zoom_in=self.zoom_in,
            on_zoom_out=self.zoom_out,
            on_reset_view=self.reset_view,
            on_load_history=self._on_menu_load_history,
        )

    def _on_menu_load_history(self, hours: float) -> None:
        """Route the 'Load Limit-Order History' action to the app-level hook."""
        cb = getattr(self, "on_load_history", None)
        if cb is not None:
            cb(hours)

    def _on_menu_timeframe(self, seconds: int) -> None:
        self.set_timeframe(seconds)
        cb = self.on_timeframe_changed
        if cb is not None:
            cb(seconds)

    def _on_menu_grid(self, on: bool) -> None:
        self.grid_visible = bool(on)
        if HAS_PYSIDE:
            self.update()

    def _on_menu_price_line(self, on: bool) -> None:
        self.price_line_visible = bool(on)
        if HAS_PYSIDE:
            self.update()

    def _on_menu_decay(self, half_life: float) -> None:
        """DOM liquidity decay preset — routed through the app-level hook so
        the engine's DOMCache config can be updated live."""
        cb = getattr(self, "on_decay_changed", None)
        if cb is not None:
            cb(half_life)
        self.dom_half_life_ref = half_life

    if HAS_PYSIDE:
        def mousePressEvent(self, event: QMouseEvent) -> None:
            if event.button() == Qt.LeftButton:
                self.is_dragging = True
                self.last_mouse_pos = event.position()
            elif event.button() == Qt.RightButton:
                if self._menu is None:
                    self._menu = self._build_menu()
                self._menu.set_timeframe_checked(self.tf_seconds)
                self._menu.exec(event.globalPos())

        def mouseMoveEvent(self, event: QMouseEvent) -> None:
            if self.is_dragging and self.last_mouse_pos:
                delta = event.position() - self.last_mouse_pos
                self.offset_x += delta.x()
                self.offset_y += delta.y()
                self.last_mouse_pos = event.position()
                self.update()

        def mouseReleaseEvent(self, event: QMouseEvent) -> None:
            if event.button() == Qt.LeftButton:
                self.is_dragging = False

        def wheelEvent(self, event: QWheelEvent) -> None:
            delta = event.angleDelta().y()
            factor = 1.1 if delta > 0 else 0.9
            if event.modifiers() & Qt.ControlModifier:
                # Vertical zoom
                self.scale_y = clamp(self.scale_y * factor, 1.0, 50.0)
            else:
                # Horizontal zoom
                self.scale_x = clamp(self.scale_x * factor, 2.0, 60.0)
            self.update()

        def paintEvent(self, event: QPaintEvent) -> None:
            painter = QPainter(self)
            painter.setRenderHint(QPainter.Antialiasing)

            w = self.width()
            h = self.height()

            # 1. Background
            painter.fillRect(0, 0, w, h, QColor(THEME.bg_main))

            # 2. Draw subtle grid
            self._draw_grid(painter, w, h)

            # 3. Draw bars & volume
            self._draw_bars(painter, w, h)

            # 4. Draw laser price line
            self._draw_price_line(painter, w, h)

            painter.end()

        def _draw_grid(self, painter: QPainter, w: int, h: int) -> None:
            if not self.grid_visible:
                return
            pen = QPen(QColor(THEME.border_subtle), 1, Qt.DotLine)
            painter.setPen(pen)
            step_y = 50
            for y in range(0, h, step_y):
                painter.drawLine(0, y, w, y)

        def _draw_bars(self, painter: QPainter, w: int, h: int) -> None:
            all_bars = self._display_series()
            if not all_bars:
                return

            mid_y = h / 2.0 + self.offset_y
            base_x = w - 100 + self.offset_x

            # Line chart: single polyline through closes
            if self.chart_type == "Line":
                pen = QPen(QColor(THEME.gold_highlight), 2)
                painter.setPen(pen)
                prev = None
                for i, bar in enumerate(reversed(all_bars)):
                    x = base_x - (i * self.scale_x)
                    if x < -50:
                        break
                    ref_p = self.current_price if self.current_price > 0 else bar.close
                    y = mid_y - (bar.close - ref_p) * self.scale_y
                    pt = QPointF(x, y)
                    if prev is not None:
                        painter.drawLine(prev, pt)
                    prev = pt
                return

            for i, bar in enumerate(reversed(all_bars)):
                x = base_x - (i * self.scale_x)
                if x < -50:
                    break

                # Bar body
                is_bull = bar.close >= bar.open
                color = QColor(THEME.bull_green if is_bull else THEME.bear_red)
                painter.setPen(QPen(color, 1))

                # Price to Y coords
                ref_p = self.current_price if self.current_price > 0 else bar.close
                y_open = mid_y - (bar.open - ref_p) * self.scale_y
                y_close = mid_y - (bar.close - ref_p) * self.scale_y
                y_high = mid_y - (bar.high - ref_p) * self.scale_y
                y_low = mid_y - (bar.low - ref_p) * self.scale_y

                half_w = max(1.5, self.scale_x * 0.35)

                if self.chart_type == "Bars (OHLC)":
                    # Classic OHLC bar: left tick = open, right tick = close
                    painter.drawLine(QPointF(x, y_high), QPointF(x, y_low))
                    painter.drawLine(QPointF(x - half_w, y_open), QPointF(x, y_open))
                    painter.drawLine(QPointF(x, y_close), QPointF(x + half_w, y_close))
                    continue

                # Wick
                painter.drawLine(QPointF(x, y_high), QPointF(x, y_low))

                # Candle body
                body_top = min(y_open, y_close)
                body_h = max(2.0, abs(y_close - y_open))
                painter.fillRect(QRectF(x - half_w, body_top, half_w * 2, body_h), color)

        def _draw_price_line(self, painter: QPainter, w: int, h: int) -> None:
            if self.current_price <= 0 or not self.price_line_visible:
                return
            mid_y = h / 2.0 + self.offset_y
            pen = QPen(QColor(THEME.gold_highlight), 1, Qt.DashLine)
            painter.setPen(pen)
            painter.drawLine(0, int(mid_y), w, int(mid_y))

            # Beacon badge on right edge
            badge_rect = QRectF(w - 75, mid_y - 10, 70, 20)
            painter.fillRect(badge_rect, QColor(THEME.gold_highlight))
            painter.setPen(QColor("#000000"))
            font = QFont("Segoe UI", 9, QFont.Bold)
            painter.setFont(font)
            painter.drawText(badge_rect, Qt.AlignCenter, f"{self.current_price:.2f}")
