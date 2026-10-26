"""Bottom status bar (Fase 6): live engine health, tick rate, feed latency,
and a slim progress strip used during limit-order history loads."""

from typing import Optional

try:
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QLabel, QProgressBar, QStatusBar, QWidget
    HAS_PYSIDE = True
except ImportError:
    HAS_PYSIDE = False
    QStatusBar = object
    QWidget = object


class TerminalStatusBar(QStatusBar if HAS_PYSIDE else object):
    """Bottom status bar reporting engine health, tick rate, and latency."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        self._last_ticks = 0
        self._rate_ema = 0.0
        if HAS_PYSIDE:
            super().__init__(parent)
            self.setSizeGripEnabled(False)
            self._setup_ui()

    def _setup_ui(self) -> None:
        self.lbl_status = QLabel("🟢 Live Connected")
        self.lbl_ticks = QLabel("Ticks: 0")
        self.lbl_rate = QLabel("Rate: --/s")
        self.lbl_fps = QLabel("Engine: O(1) Bounded")

        # Slim indeterminate-height progress strip (hidden until a load runs)
        self.progress = QProgressBar()
        self.progress.setObjectName("ProgressStrip")
        self.progress.setFixedHeight(3)
        self.progress.setTextVisible(False)
        self.progress.setRange(0, 0)      # busy indicator while loading
        self.progress.hide()

        self.addWidget(self.lbl_status, stretch=1)
        self.addPermanentWidget(self.lbl_ticks)
        self.addPermanentWidget(self.lbl_rate)
        self.addPermanentWidget(self.lbl_fps)

    def update_ticks(self, total_ticks: int) -> None:
        if not HAS_PYSIDE:
            return
        self.lbl_ticks.setText(f"Ticks: {total_ticks:,}")
        # Exponential-moving-average tick rate over ~1 s windows
        delta = max(0, total_ticks - self._last_ticks)
        self._last_ticks = total_ticks
        self._rate_ema = 0.85 * self._rate_ema + 0.15 * delta * 30.0
        self.lbl_rate.setText(f"Rate: {self._rate_ema:,.0f}/s")

    def set_busy(self, busy: bool, label: str = "") -> None:
        """Show/hide the thin loading strip (Fase 6)."""
        if not HAS_PYSIDE:
            return
        if busy:
            self.progress.setRange(0, 0)
            self.progress.show()
            if label:
                self.showMessage(label)
        else:
            self.progress.hide()
            self.clearMessage()
