"""Bookmap-style Depth-of-Market panel.

Renders the *reconstructed* L2 limit-order picture from ``DOMCache`` as a
classic DOM ladder: asks above, bids below, with horizontal volume bars whose
length is proportional to estimated resting liquidity at each price level.

Because MT5 brokers never expose the real order book, every row here is an
inference derived from executed trades (sweep / touch / decay signals), not a
guaranteed quote — exactly like Bookmap's "liquidity estimation" mode when no
exchange depth feed is available.
"""

from typing import List, Optional

try:
    from PySide6.QtCore import QRectF, Qt, QTimer
    from PySide6.QtGui import QColor, QFont, QPainter, QPaintEvent, QPen
    from PySide6.QtWidgets import QWidget
    HAS_PYSIDE = True
except ImportError:
    HAS_PYSIDE = False
    QWidget = object

from tmap.core.dom_cache import DOMCache, LiquidityLevel
from tmap.ui.theme import THEME


class DOMLadderPanel(QWidget if HAS_PYSIDE else object):
    """Live limit-order ladder widget fed directly by a ``DOMCache`` instance."""

    ROW_H = 18          # pixels per price level row
    PRICE_COL_W = 90    # reserved width for the price column (right side)
    MAX_ROWS = 40       # max levels rendered per side

    def __init__(self, dom_cache: DOMCache, parent: Optional["QWidget"] = None) -> None:
        if HAS_PYSIDE:
            super().__init__(parent)
            self.setMinimumWidth(260)
            self.setAttribute(Qt.WA_OpaquePaintEvent, True)
            # Redraw on a UI timer so idle walls visibly fade with time decay
            self._timer = QTimer(self)
            self._timer.timeout.connect(self._on_refresh)
            self._timer.start(250)
        self.dom_cache = dom_cache
        self.last_price: float = 0.0

    # ------------------------------------------------------------------ data

    def set_last_price(self, price: float) -> None:
        self.last_price = price

    def update_ladder(self) -> None:
        if HAS_PYSIDE:
            self.update()

    def _rows(self) -> List[tuple]:
        """Build render rows: (level, kind) where kind in {'bid','ask'}."""
        ladder = self.dom_cache.get_ladder(depth=self.MAX_ROWS)
        rows = [(lvl, "ask") for lvl in reversed(ladder["asks"])]
        rows += [(lvl, "bid") for lvl in ladder["bids"]]
        return rows

    # ----------------------------------------------------------------- paint

    def _on_refresh(self) -> None:
        if HAS_PYSIDE:
            self.update()

    if HAS_PYSIDE:
        def paintEvent(self, event: QPaintEvent) -> None:
            painter = QPainter(self)
            w, h = self.width(), self.height()
            painter.fillRect(0, 0, w, h, QColor(THEME.bg_surface))

            rows = self._rows()
            if not rows:
                painter.setPen(QColor(THEME.text_muted))
                painter.drawText(QRectF(0, 0, w, h), Qt.AlignCenter,
                                 "Menunggu data likuiditas…")
                painter.end()
                return

            # Apply time decay before rendering so stale walls shrink live
            self.dom_cache.apply_time_decay()
            rows = self._rows()
            if not rows:
                painter.end()
                return

            max_vol = max(l.volume for l, _ in rows) or 1.0
            bar_area_w = w - self.PRICE_COL_W - 16

            # Center the ladder around best bid/ask boundary
            n_asks = sum(1 for _, k in rows if k == "ask")
            total_h = len(rows) * self.ROW_H
            y0 = max(4.0, (h - total_h) / 2.0)
            mid_y = y0 + n_asks * self.ROW_H

            font = QFont("Consolas", 9)
            painter.setFont(font)

            for i, (lvl, kind) in enumerate(rows):
                y = y0 + i * self.ROW_H
                bar_w = (lvl.volume / max_vol) * bar_area_w
                color = QColor(THEME.bull_green) if kind == "bid" else QColor(THEME.bear_red)

                # Volume bar (proportional, Bookmap-style heat row)
                fill = QColor(color)
                fill.setAlpha(70 + int(150 * (lvl.volume / max_vol)))
                painter.fillRect(QRectF(8, y + 2, bar_w, self.ROW_H - 4), fill)

                # Raw cumulative ghost bar (shows how much has been absorbed)
                raw_w = min((lvl.raw_volume / max(lvl.volume, 1e-9), 3.0)) * bar_w
                painter.setPen(QPen(QColor(THEME.gold_highlight), 1, Qt.DotLine))
                painter.drawLine(int(8 + raw_w), int(y + 1), int(8 + raw_w), int(y + self.ROW_H - 1))

                # Volume text inside bar
                painter.setPen(QColor(THEME.text_primary))
                label = f"{lvl.volume:.1f}"
                if lvl.touches >= 3:
                    label += f"  T{lvl.touches}"   # absorbing wall marker
                if lvl.sweeps > 0:
                    label += f"  S{lvl.sweeps}"    # swept-through marker
                painter.drawText(QRectF(10, y, bar_area_w, self.ROW_H),
                                 Qt.AlignVCenter | Qt.AlignLeft, label)

                # Price column
                painter.setPen(color)
                painter.drawText(QRectF(w - self.PRICE_COL_W, y, self.PRICE_COL_W - 4, self.ROW_H),
                                 Qt.AlignVCenter | Qt.AlignRight, f"{lvl.price:.2f}")

            # Last-price separator line at the bid/ask boundary
            pen = QPen(QColor(THEME.gold_highlight), 1, Qt.DashLine)
            painter.setPen(pen)
            painter.drawLine(0, int(mid_y), w, int(mid_y))
            if self.last_price > 0:
                painter.setPen(QColor(THEME.gold_highlight))
                painter.drawText(QRectF(0, mid_y - 16, bar_area_w, 15),
                                 Qt.AlignLeft, f"Last: {self.last_price:.2f}")

            # Header
            painter.setPen(QColor(THEME.text_secondary))
            painter.drawText(8, 14, "L2 DOM (estimasi dari tape)")

            painter.end()
