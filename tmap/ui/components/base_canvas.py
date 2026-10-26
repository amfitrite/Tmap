import math
from typing import Deque, List, Optional
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
from tmap.ui.theme import THEME
from tmap.utils.math_helpers import clamp


class ChartCanvas(QWidget if HAS_PYSIDE else object):
    """
    Primary Order Flow & Microstructure Visual Canvas.
    Supports high-refresh rendering, dynamic scaling, and interactive pan/zoom.
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

    if HAS_PYSIDE:
        def mousePressEvent(self, event: QMouseEvent) -> None:
            if event.button() == Qt.LeftButton:
                self.is_dragging = True
                self.last_mouse_pos = event.position()

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
            pen = QPen(QColor(THEME.border_subtle), 1, Qt.DotLine)
            painter.setPen(pen)
            step_y = 50
            for y in range(0, h, step_y):
                painter.drawLine(0, y, w, y)

        def _draw_bars(self, painter: QPainter, w: int, h: int) -> None:
            all_bars = self.bars + ([self.current_bar] if self.current_bar else [])
            if not all_bars:
                return

            mid_y = h / 2.0 + self.offset_y
            base_x = w - 100 + self.offset_x

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

                # Wick
                painter.drawLine(QPointF(x, y_high), QPointF(x, y_low))

                # Candle body
                body_top = min(y_open, y_close)
                body_h = max(2.0, abs(y_close - y_open))
                painter.fillRect(QRectF(x - self.scale_x * 0.35, body_top, self.scale_x * 0.7, body_h), color)

        def _draw_price_line(self, painter: QPainter, w: int, h: int) -> None:
            if self.current_price <= 0:
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
