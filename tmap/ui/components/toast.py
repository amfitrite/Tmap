"""Small bottom-right toast notifications (Fase 4.2).

Transient feedback chips ("Loaded 10 800 ticks · Synthetic") that fade out
after a few seconds — instead of burying the user in status-log lines. Purely
token-driven styling; no hardcoded colors here.
"""

from typing import Optional

try:
    from PySide6.QtCore import Qt, QTimer
    from PySide6.QtWidgets import QGraphicsOpacityEffect, QLabel
    HAS_PYSIDE = True
except ImportError:
    HAS_PYSIDE = False
    QLabel = object


class Toast(QLabel if HAS_PYSIDE else object):
    """Auto-fading notification bubble anchored to its parent's corner."""

    def __init__(self, text: str, parent: Optional["QWidget"] = None,
                 duration_ms: int = 3000, accent: str = "border_accent") -> None:
        """*accent* names a THEME token (e.g. ``"cyan_info"``) — never a raw
        hex literal; the design-token rule (§1.1) keeps toasts theme-driven."""
        if not HAS_PYSIDE:
            return
        super().__init__(text, parent)
        self.setObjectName("Toast")
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        from tmap.ui.theme import THEME
        accent_hex = getattr(THEME, accent, THEME.border_accent)
        self.setStyleSheet(
            f"background: rgba(26,30,38,235); color: {THEME.text_primary};"
            f"border: 1px solid {accent_hex}; border-radius: 8px;"
            f"padding: 6px 12px; font-size: 11px;")
        self.adjustSize()
        self._effect = QGraphicsOpacityEffect(self)
        self.setGraphicsEffect(self._effect)
        self._effect.setOpacity(1.0)
        self._reposition()
        self.show()
        self.raise_()
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._fade_out)
        self._timer.start(duration_ms)

    def _reposition(self) -> None:
        p = self.parentWidget()
        if p is None:
            return
        margin = 16
        self.move(p.width() - self.width() - margin,
                  p.height() - self.height() - margin)

    def resizeEvent(self, event) -> None:  # keep glued to corner
        super().resizeEvent(event)
        self._reposition()

    def _fade_out(self) -> None:
        """Simple stepped opacity fade (no QPropertyAnimation dependency)."""
        self._fade_step = getattr(self, "_fade_step", 10)
        self._fade_step -= 1
        if self._fade_step <= 0:
            self.deleteLater()
            return
        self._effect.setOpacity(self._fade_step / 10.0)
        QTimer.singleShot(40, self._fade_out)


def show_toast(parent: "QWidget", text: str, duration_ms: int = 3000,
               accent: str = "border_accent") -> None:
    """Convenience: pop a transient toast in *parent*'s bottom-right corner.

    *accent* is a THEME attribute name (e.g. ``"cyan_info"``), never raw hex.
    """
    if not HAS_PYSIDE or parent is None:
        return
    t = Toast(text, parent=parent, duration_ms=duration_ms, accent=accent)
    t.setParent(parent)          # re-anchor if parent got resized meanwhile
    t._reposition()
