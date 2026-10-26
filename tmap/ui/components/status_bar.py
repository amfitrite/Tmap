from typing import Optional

try:
    from PySide6.QtWidgets import QLabel, QStatusBar, QWidget
    HAS_PYSIDE = True
except ImportError:
    HAS_PYSIDE = False
    QStatusBar = object


class TerminalStatusBar(QStatusBar if HAS_PYSIDE else object):
    """Bottom status bar reporting engine health, tick rate, and latency."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        if HAS_PYSIDE:
            super().__init__(parent)
            self._setup_ui()

    def _setup_ui(self) -> None:
        self.lbl_status = QLabel("🟢 Live Connected")
        self.lbl_ticks = QLabel("Ticks: 0")
        self.lbl_fps = QLabel("Engine: O(1) Bounded")

        self.addWidget(self.lbl_status)
        self.addPermanentWidget(self.lbl_ticks)
        self.addPermanentWidget(self.lbl_fps)

    def update_ticks(self, total_ticks: int) -> None:
        if HAS_PYSIDE:
            self.lbl_ticks.setText(f"Ticks: {total_ticks:,}")
