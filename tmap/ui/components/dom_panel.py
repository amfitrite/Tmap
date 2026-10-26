"""Bookmap/Jigsaw-style Depth-of-Market panel (Fase 3).

Two-sided limit-order ladder reconstructed from ``DOMCache``: bids on the
left (green, bars mirror outward from centre), asks on the right (red), a
spread gap in between, imbalance highlighting (bid ≥ 3× ask → cyan border,
Jigsaw pattern), an animated header imbalance meter, and smooth 80 ms
ease-out bar-width animation so walls visibly grow/fade instead of flicker.

Because MT5 brokers never expose the real order book, every row here is an
inference derived from executed trades (sweep / touch / decay signals), not a
guaranteed quote — exactly like Bookmap's "liquidity estimation" mode when no
exchange depth feed is available.
"""

import math
from typing import Dict, List, Optional, Tuple

try:
    from PySide6.QtCore import (QEasingCurve, QPropertyAnimation, QRectF, Qt,
                                QTimer)
    from PySide6.QtGui import (QBrush, QColor, QLinearGradient, QPainter,
                               QPaintEvent, QPen)
    from PySide6.QtWidgets import QWidget
    HAS_PYSIDE = True
except ImportError:
    HAS_PYSIDE = False
    QWidget = object

from tmap.core.dom_cache import DOMCache, LiquidityLevel
from tmap.ui.theme import RADIUS, THEME, mono_font


def find_imbalances(rows: List[Tuple[LiquidityLevel, str]],
                    ratio: float = 3.0) -> set:
    """Pure helper: Jigsaw-style imbalance detection (Fase 3).

    A level is flagged when its volume is >= ``ratio`` times the volume of
    the nearest level on the opposite side *at the same price* (best bid vs
    best ask for the top rows, then neighbour-by-neighbour outward). Returns
    the set of ``(price, kind)`` tuples to highlight. Kept Qt-free so it can
    be unit-tested headless.
    """
    bids = {round(l.price, 6): l.volume for l, k in rows if k == "bid"}
    asks = {round(l.price, 6): l.volume for l, k in rows if k == "ask"}
    flags = set()
    # Compare each displayed level against the nearest opposite-side level
    bid_prices = sorted(bids.keys(), reverse=True)   # best first
    ask_prices = sorted(asks.keys())                 # best first
    for i, p in enumerate(bid_prices):
        opp = ask_prices[i] if i < len(ask_prices) else None
        ov = asks.get(opp, 0.0) if opp is not None else 0.0
        if ov <= 0 or bids[p] >= ratio * max(ov, 1e-9):
            flags.add((p, "bid"))
    for i, p in enumerate(ask_prices):
        opp = bid_prices[i] if i < len(bid_prices) else None
        ov = bids.get(opp, 0.0) if opp is not None else 0.0
        if ov <= 0 or asks[p] >= ratio * max(ov, 1e-9):
            flags.add((p, "ask"))
    return flags


class DOMLadderPanel(QWidget if HAS_PYSIDE else object):
    """Live limit-order ladder widget fed directly by a ``DOMCache`` instance."""

    ROW_H = 18          # pixels per price level row
    PRICE_COL_W = 74    # reserved width for the centre price column
    MAX_ROWS = 24       # max levels rendered per side
    ANIM_MS = 80        # bar-width ease-out animation window (Fase 3)

    def __init__(self, dom_cache: DOMCache, parent: Optional["QWidget"] = None) -> None:
        self.dom_cache = dom_cache
        self.last_price: float = 0.0
        self.symbol: str = ""
        # Animated state: previous & target fraction per (price, kind) plus the
        # timestamp when the target last changed — gives the 80ms ease-out.
        self._prev_frac: Dict[tuple, float] = {}
        self._target_frac: Dict[tuple, float] = {}
        self._change_ts: Dict[tuple, float] = {}
        self._meter_prev = 0.5
        self._meter_target = 0.5
        self._meter_ts = 0.0
        if HAS_PYSIDE:
            super().__init__(parent)
            self.setMinimumWidth(340)
            self.setAttribute(Qt.WA_OpaquePaintEvent, True)
            # Redraw on a UI timer so idle walls visibly fade with time decay
            # and width animations interpolate smoothly (~60 fps coalesced).
            self._timer = QTimer(self)
            self._timer.timeout.connect(self._on_refresh)
            self._timer.start(120)

    # ------------------------------------------------------------------ data

    def set_last_price(self, price: float) -> None:
        self.last_price = price

    def update_ladder(self) -> None:
        if HAS_PYSIDE:
            self.update()

    def _rows(self) -> List[Tuple[LiquidityLevel, str]]:
        """Build render rows: (level, kind) where kind in {'bid','ask'}."""
        ladder = self.dom_cache.get_ladder(depth=self.MAX_ROWS)
        rows = [(lvl, "ask") for lvl in reversed(ladder["asks"])]
        rows += [(lvl, "bid") for lvl in ladder["bids"]]
        return rows

    # ------------------------------------------------------------- animation

    def _animated_frac(self, key: tuple, target: float, now: float) -> float:
        """Interpolate toward ``target`` over ANIM_MS with ease-out (Fase 3)."""
        prev = self._target_frac.get(key, target)
        if abs(prev - target) > 1e-6:
            self._prev_frac[key] = self.current_value(key, now)
            self._target_frac[key] = target
            self._change_ts[key] = now
        started = self._change_ts.get(key, now)
        u = min(1.0, max(0.0, (now - started) * 1000.0 / self.ANIM_MS))
        eased = 1.0 - (1.0 - u) ** 3          # cubic ease-out
        p = self._prev_frac.get(key, target)
        return p + (target - p) * eased

    def current_value(self, key: tuple, now: float) -> float:
        started = self._change_ts.get(key, now)
        u = min(1.0, max(0.0, (now - started) * 1000.0 / self.ANIM_MS))
        eased = 1.0 - (1.0 - u) ** 3
        p = self._prev_frac.get(key, self._target_frac.get(key, 0.0))
        t = self._target_frac.get(key, 0.0)
        return p + (t - p) * eased

    # ----------------------------------------------------------------- paint

    def _on_refresh(self) -> None:
        if HAS_PYSIDE:
            self.update()

    if HAS_PYSIDE:
        def paintEvent(self, event: QPaintEvent) -> None:
            import time as _time
            now = _time.monotonic()
            painter = QPainter(self)
            w, h = self.width(), self.height()
            painter.setRenderHint(QPainter.Antialiasing, False)
            painter.setRenderHint(QPainter.TextAntialiasing, True)
            painter.fillRect(0, 0, w, h, QColor(THEME.bg_surface))

            # Apply time decay before rendering so stale walls shrink live
            self.dom_cache.apply_time_decay()
            rows = self._rows()

            # ---- Header: symbol + imbalance meter (Fase 3) ---------------
            self._paint_header(painter, w, now)

            if not rows:
                painter.setPen(QColor(THEME.text_muted))
                painter.drawText(QRectF(0, 0, w, h), Qt.AlignCenter,
                                 "Menunggu data likuiditas…")
                painter.end()
                return

            max_vol = max(l.volume for l, _ in rows) or 1.0
            half_area = (w - self.PRICE_COL_W) / 2.0 - 10
            cx = w / 2.0
            price_x0 = cx - self.PRICE_COL_W / 2.0

            n_asks = sum(1 for _, k in rows if k == "ask")
            total_h = len(rows) * self.ROW_H
            y0 = max(34.0, (h - total_h) / 2.0 + 12)
            mid_y = y0 + n_asks * self.ROW_H

            imbal = find_imbalances(rows)
            font = mono_font("ladder")
            painter.setFont(font)
            price_font = mono_font("ladder", bold=True)

            for i, (lvl, kind) in enumerate(rows):
                y = y0 + i * self.ROW_H
                key = (round(lvl.price, 6), kind)
                frac = lvl.volume / max_vol
                anim = self._animated_frac(key, frac, now)
                bar_w = max(2.0, anim * half_area)
                base_col = QColor(THEME.bull_green if kind == "bid"
                                  else THEME.bear_red)

                # Mirrored gradient bar: grows OUTWARD from the centre price
                # column, saturated near the centre, faded at the tip
                grad = QLinearGradient()
                if kind == "bid":
                    bx0, bx1 = cx - self.PRICE_COL_W / 2.0 - 8 - bar_w, \
                               cx - self.PRICE_COL_W / 2.0 - 8
                    grad = QLinearGradient(bx1, 0, bx0, 0)
                else:
                    bx0, bx1 = cx + self.PRICE_COL_W / 2.0 + 8, \
                               cx + self.PRICE_COL_W / 2.0 + 8 + bar_w
                    grad = QLinearGradient(bx0, 0, bx1, 0)
                c_hi, c_lo = QColor(base_col), QColor(base_col)
                c_hi.setAlpha(80 + int(150 * min(anim, 1.0)))
                c_lo.setAlpha(int(c_hi.alpha() * 0.35))
                grad.setColorAt(0.0, c_hi)
                grad.setColorAt(1.0, c_lo)
                painter.fillRect(QRectF(min(bx0, bx1), y + 2, bar_w, self.ROW_H - 4),
                                 QBrush(grad))

                # Imbalance highlight: cyan 1-px border (Jigsaw pattern)
                if key in imbal:
                    painter.setPen(QPen(QColor(THEME.cyan_info), 1))
                    painter.setBrush(Qt.NoBrush)
                    painter.drawRect(QRectF(min(bx0, bx1) - 0.5, y + 1.5,
                                            bar_w + 1, self.ROW_H - 3))

                # Raw cumulative ghost line (absorption tell)
                raw_ratio = min((lvl.raw_volume / max(lvl.volume, 1e-9), 3.0))
                gx_off = raw_ratio * bar_w
                gx = int(math.floor((bx1 + gx_off) if kind == "bid"
                                    else (bx0 + gx_off)) + 0.5)
                painter.setPen(QPen(QColor(THEME.gold_highlight), 1, Qt.DotLine))
                painter.drawLine(gx, int(y + 1), gx, int(y + self.ROW_H - 1))

                # Volume text inside bar, tabular digits
                painter.setPen(QColor(THEME.text_primary))
                label = f"{lvl.volume:.1f}"
                pill = None
                if lvl.touches >= 3:
                    pill = ("T", THEME.gold_highlight)      # absorbing wall
                elif lvl.sweeps > 0:
                    pill = ("S", THEME.bear_red)             # swept level
                tx = bx0 + 4 if kind == "bid" else bx0 + 4
                painter.drawText(QRectF(tx, y, half_area, self.ROW_H),
                                 Qt.AlignVCenter | Qt.AlignLeft, label)
                if pill:
                    px = (bx0 + 4 + 46) if kind == "bid" else (bx0 + 4 + 46)
                    pr = QRectF(px, y + 3, 16, self.ROW_H - 6)
                    painter.setPen(Qt.NoPen)
                    pc = QColor(pill[1]); pc.setAlpha(200)
                    painter.setBrush(pc)
                    painter.drawRoundedRect(pr, RADIUS["badge"], RADIUS["badge"])
                    painter.setBrush(Qt.NoBrush)
                    painter.setPen(QColor(THEME.badge_text))
                    painter.drawText(pr, Qt.AlignCenter, pill[0])
                    painter.setPen(QColor(THEME.text_primary))

                # Centre price column (bold tabular mono)
                painter.setFont(price_font)
                painter.setPen(base_col)
                painter.drawText(QRectF(price_x0, y, self.PRICE_COL_W, self.ROW_H),
                                 Qt.AlignVCenter | Qt.AlignCenter, f"{lvl.price:.2f}")
                painter.setFont(font)

            # Last-price separator at the bid/ask boundary
            painter.setPen(QPen(QColor(THEME.gold_highlight), 1, Qt.DashLine))
            painter.drawLine(0, int(mid_y), w, int(mid_y))

            # Column captions above the split
            painter.setPen(QColor(THEME.text_muted))
            painter.drawText(QRectF(8, y0 - 16, half_area, 14),
                             Qt.AlignLeft, "BID (estimasi tape)")
            painter.drawText(QRectF(cx + self.PRICE_COL_W / 2.0, y0 - 16,
                                    half_area, 14), Qt.AlignRight, "ASK")

            painter.end()

        # ------------------------------------------------------------ header
        def _paint_header(self, painter: QPainter, w: int, now: float) -> None:
            liq = self.dom_cache.total_resting_liquidity()
            imb = liq["bid_imbalance"]
            if abs(self._meter_target - imb) > 1e-6:
                self._meter_prev = imb if now - self._meter_ts > 1 else self._meter_prev
                self._meter_target = imb
                self._meter_ts = now
            u = min(1.0, max(0.0, (now - self._meter_ts) * 1000.0 / 250.0))
            eased = 1.0 - (1.0 - u) ** 3
            disp = self._meter_prev + (self._meter_target - self._meter_prev) * eased

            title = f"L2 DOM · {self.symbol}".strip()
            painter.setPen(QColor(THEME.text_secondary))
            painter.setFont(mono_font("header", bold=True))
            painter.drawText(10, 16, title)

            # Horizontal imbalance meter (Bookmap-style proportion bar)
            mw, mh = 120, 6
            mx, my = w - mw - 10, 10
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(THEME.bg_panel))
            painter.drawRoundedRect(QRectF(mx, my, mw, mh), 3, 3)
            bid_w = mw * max(0.05, min(0.95, disp))
            bc = QColor(THEME.bull_green); bc.setAlpha(220)
            painter.setBrush(bc)
            painter.drawRoundedRect(QRectF(mx, my, bid_w, mh), 3, 3)
            ac = QColor(THEME.bear_red); ac.setAlpha(220)
            painter.setBrush(ac)
            painter.drawRoundedRect(QRectF(mx + bid_w, my, mw - bid_w, mh), 3, 3)
            painter.setBrush(Qt.NoPen and Qt.NoBrush)
            painter.setPen(QColor(THEME.text_muted))
            painter.setFont(mono_font("badge"))
            painter.drawText(QRectF(mx - 60, my - 3, 58, mh + 6), Qt.AlignRight | Qt.AlignVCenter,
                             f"{disp * 100:.0f}%B")
            painter.setPen(QColor(THEME.text_secondary))
