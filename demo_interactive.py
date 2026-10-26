"""
Tmap Interactive Demo — LIVE liquidity heatmap (PySide6, QPainter).

Pipeline (real project modules, accelerated mock feed or live MT5):
    MT5TCPFeed/MockMarketFeed ticks -> RecordingSession (DOMCache -> frames)
                                   -> TileStore (tiles flushed to disk every 10s)
                                   -> HeatmapCanvas paints viewport via store.query_window()

Controls:
    drag mouse   : pan time/price
    wheel        : zoom time axis
    Ctrl+wheel   : zoom price axis
    R            : reset camera
    Q / close    : quit

Usage:
    python demo_interactive.py          # Interactive mock feed
    python demo_interactive.py --mt5    # Connect to live MetaTrader 5 Bridge on port 5555
"""
import math
import sys
import tempfile
import time

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPen
from PySide6.QtWidgets import QApplication, QWidget

from tmap.core.heatmap_store import TileStore
from tmap.network.mock_feed import MockMarketFeed

TICK_SIZE = 0.01
BASE_PPS_X = 8.0     # pixels per second at scale 1
BASE_PPS_Y = 1200.0   # pixels per $1 at scale 1 (~$0.7 price window)


class HeatmapCanvas(QWidget):
    """Renders the persistent tile history as a scrolling heatmap image."""

    def __init__(self, store: TileStore, symbol: str, parent=None):
        super().__init__(parent)
        self.store = store
        self.symbol = symbol
        self.setMouseTracking(True)
        self.setAttribute(Qt.WA_OpaquePaintEvent, True)
        self.setStyleSheet("background:#0b0e15;")

        # Camera: center on latest data
        self.center_t = time.time()
        self.center_p = 2750.0
        self.scale_x = 1.0
        self.scale_y = 1.0
        self.follow = True

        self._dragging = False
        self._last_pos = None

        self.img: QImage | None = None
        self.last_price = 0.0

        self.repaint_timer = QTimer(self)
        self.repaint_timer.timeout.connect(self.update)
        self.repaint_timer.start(33)

    # ------------------------------------------------------------- interaction
    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._dragging = True
            self._last_pos = e.position()
            self.follow = False

    def mouseMoveEvent(self, e):
        if self._dragging and self._last_pos is not None:
            d = e.position() - self._last_pos
            self.center_t -= d.x() / (BASE_PPS_X * self.scale_x)
            self.center_p += d.y() / (BASE_PPS_Y * self.scale_y)
            self._last_pos = e.position()

    def mouseReleaseEvent(self, e):
        self._dragging = False

    def wheelEvent(self, e):
        f = 1.15 if e.angleDelta().y() > 0 else 1 / 1.15
        if e.modifiers() & Qt.ControlModifier:
            self.scale_y = max(0.2, min(20.0, self.scale_y * f))
        else:
            self.scale_x = max(0.2, min(10.0, self.scale_x * f))

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_R:
            self.scale_x = self.scale_y = 1.0
            self.follow = True
        super().keyPressEvent(e)

    # ------------------------------------------------------------------ render
    def paintEvent(self, _):
        w, h = self.width(), self.height()
        ppsx = BASE_PPS_X * self.scale_x
        ppsy = BASE_PPS_Y * self.scale_y
        half_t = w / 2 / ppsx
        half_p = h / 2 / ppsy
        t0, t1 = self.center_t - half_t, self.center_t + half_t
        plo, phi = self.center_p - half_p, self.center_p + half_p

        painter = QPainter(self)
        painter.fillRect(0, 0, w, h, QColor("#0b0e15"))

        frames = [f for f in self.store.query_window(self.symbol, t0, t1, plo, phi)
                  if f.cells or f.trades]

        if frames:
            # grid extents snapped to tick_size
            idx_lo = int(round(plo / TICK_SIZE))
            idx_hi = int(round(phi / TICK_SIZE))
            n_rows = max(1, idx_hi - idx_lo + 1)
            strength = [[0.0] * n_rows for _ in range(w)]
            side_map = [[0] * n_rows for _ in range(w)]  # 1 buy-triangle, -1 sell

            maxv = 1.0
            for f in frames:
                col = int((f.timestamp - t0) / (t1 - t0) * w)
                if not (0 <= col < w):
                    continue
                for pi, c in f.cells.items():
                    r = pi - idx_lo
                    if 0 <= r < n_rows:
                        v = c.bid + c.ask
                        if v > strength[col][r]:
                            strength[col][r] = v
                for tp, vol, sd in f.trades:
                    r = tp - idx_lo
                    if 0 <= r < n_rows:
                        side_map[col][r] = 1 if sd == 1 else -1
                        if vol > maxv:
                            pass
            # log-normalised colour ramp
            norm = max(math.log1p(max(max(r) for r in strength)), 1e-9)
            painter.setPen(Qt.NoPen)
            for x, row in enumerate(strength):
                for r, v in enumerate(row):
                    if v <= 0:
                        continue
                    i = min(1.0, math.log1p(v) / norm)
                    y = h - 1 - int((r * TICK_SIZE - plo) / (phi - plo) * h)
                    alpha = int((0.10 + i * 0.9) * 255)
                    col_b = QColor(0, 200, 140, alpha)   # bid liquidity
                    col_a = QColor(230, 60, 90, alpha)   # ask liquidity
                    painter.setBrush(col_b if i < 0.55 else col_a)
                    painter.drawRect(x, y, 1, max(1, int(ppsy * TICK_SIZE)))
            # trade aggressor markers
            for x, row in enumerate(side_map):
                for r, sgn in enumerate(row):
                    if sgn == 0:
                        continue
                    y = h - 1 - int((r * TICK_SIZE - plo) / (phi - plo) * h)
                    painter.setPen(QPen(QColor("#00ffaa") if sgn > 0 else QColor("#ff4d6d"), 2))
                    dy = -4 if sgn > 0 else 4
                    painter.drawLine(x, y, x, y + dy)
            painter.setPen(Qt.NoPen)

            # mid-price polyline
            painter.setPen(QPen(QColor("#ffd54f"), 1.6))
            prev = None
            for f in frames:
                x = int((f.timestamp - t0) / (t1 - t0) * w)
                y = int((phi - f.mid_price) / (phi - plo) * h)
                if prev:
                    painter.drawLine(prev[0], prev[1], x, y)
                prev = (x, y)

        # overlays
        painter.setPen(QColor("#8899aa"))
        painter.setFont(QFont("DejaVu Sans", 9))
        painter.drawText(10, 18, f"Tmap LIVE — {self.symbol} | tiles={len(self.store.list_tiles(self.symbol))} "
                                f"| window={t1-t0:.1f}s x ${phi-plo:.2f}")
        if self.last_price:
            y = int((phi - self.last_price) / (phi - plo) * h)
            if 0 <= y < h:
                painter.setPen(QPen(QColor("#ffffff"), 1, Qt.DotLine))
                painter.drawLine(0, y, w, y)
                painter.setPen(QColor("#ffffff"))
                painter.drawText(w - 70, y - 3, f"{self.last_price:.2f}")
        painter.end()


def main():
    use_mt5 = "--mt5" in sys.argv or "--bridge" in sys.argv
    auto_close = 0.0
    for arg in sys.argv[1:]:
        if arg not in ("--mt5", "--bridge", "--mock"):
            try:
                auto_close = float(arg)
            except ValueError:
                pass

    root = tempfile.mkdtemp(prefix="tmap_live_")
    store = TileStore(root=root, tile_seconds=10.0, frame_interval_s=0.25)
    session = store.open_session(symbol="XAUUSD", tick_size=TICK_SIZE)

    app = QApplication(sys.argv)
    app.setStyle("Fusion")

    if use_mt5:
        from tmap.network.mt5_feed import MT5TCPFeed
        feed = MT5TCPFeed(host="127.0.0.1", port=5555)
        title = "Tmap — [MT5 Bridge :5555 LIVE] Interactive Liquidity Heatmap"
    else:
        feed = MockMarketFeed(symbol="XAUUSD", initial_price=2750.0, tick_interval_ms=20)
        title = "Tmap — [Synthetic Mock] Interactive Liquidity Heatmap (use --mt5 for MT5 live)"

    win = HeatmapCanvas(store, "XAUUSD")
    win.setWindowTitle(title)
    win.resize(1400, 850)

    def on_tick(tk):
        session.ingest(tk)
        win.last_price = tk.price
        if win.follow:
            win.center_t = tk.timestamp
            win.center_p = tk.price

    def on_depth(dp):
        session.dom.process_depth(dp)

    feed.on_tick = on_tick
    feed.on_depth = on_depth
    win.show()
    feed.start()

    if auto_close > 0:
        QTimer.singleShot(int(auto_close * 1000), app.quit)
    rc = app.exec()
    feed.stop()
    tiles = session.finish()
    print(f"Done. {len(tiles)} tiles written to {root}")
    sys.exit(rc)


if __name__ == "__main__":
    main()
