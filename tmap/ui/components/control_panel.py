from typing import Callable, Optional

try:
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QComboBox, QHBoxLayout, QLabel, QPushButton, QSlider, QWidget
    HAS_PYSIDE = True
except ImportError:
    HAS_PYSIDE = False
    QWidget = object

from tmap.core.types import MarketSnapshot
from tmap.ui.theme import THEME


class ControlPanel(QWidget if HAS_PYSIDE else object):
    """
    Top navigation and parameter adjustments toolbar.
    Adheres to IControlPanel protocol contract.
    """

    def __init__(
        self,
        on_toggle_feed: Optional[Callable[[], None]] = None,
        on_reset_view: Optional[Callable[[], None]] = None,
        on_change_source: Optional[Callable[[str], None]] = None,
        initial_source: str = "MT5 Bridge (5555)",
        parent: Optional[QWidget] = None,
    ) -> None:
        if HAS_PYSIDE:
            super().__init__(parent)
            self.on_toggle_feed = on_toggle_feed
            self.on_reset_view = on_reset_view
            self.on_change_source = on_change_source
            self._initial_source = initial_source
            self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 6, 12, 6)
        layout.setSpacing(10)

        # Title / Brand
        self.lbl_brand = QLabel("TMAP")
        self.lbl_brand.setStyleSheet("font-weight: bold; font-size: 13px; color: #3B82F6;")
        layout.addWidget(self.lbl_brand)

        # Feed Source Selector (MT5 Bridge vs Mock Feed)
        self.lbl_src = QLabel("Source:")
        self.lbl_src.setStyleSheet("color: #9CA3AF; font-size: 11px;")
        layout.addWidget(self.lbl_src)

        self.combo_source = QComboBox()
        self.combo_source.addItems(["MT5 Bridge (5555)", "Synthetic Mock Feed"])
        if self._initial_source in ["MT5 Bridge (5555)", "Synthetic Mock Feed"]:
            self.combo_source.setCurrentText(self._initial_source)
        self.combo_source.setStyleSheet(
            "QComboBox { background: #1A1E26; color: #F9FAFB; border: 1px solid #2B3240; "
            "border-radius: 4px; padding: 4px 8px; font-weight: 500; font-size: 11px; }"
            "QComboBox::drop-down { border: none; width: 16px; }"
            "QComboBox QAbstractItemView { background: #14171D; color: #F9FAFB; selection-background-color: #3B82F6; }"
        )
        self.combo_source.currentTextChanged.connect(self._on_source_changed)
        layout.addWidget(self.combo_source)

        # Feed toggle button
        self.btn_feed = QPushButton("⏸ Pause Feed")
        if self.on_toggle_feed:
            self.btn_feed.clicked.connect(self.on_toggle_feed)
        layout.addWidget(self.btn_feed)

        # Reset camera button
        self.btn_center = QPushButton("🎯 Center Price")
        if self.on_reset_view:
            self.btn_center.clicked.connect(self.on_reset_view)
        layout.addWidget(self.btn_center)

        layout.addStretch()

        # Market metrics widgets
        self.lbl_price = QLabel("Price: --")
        self.lbl_price.setStyleSheet("font-weight: bold; font-size: 12px;")
        layout.addWidget(self.lbl_price)

        self.lbl_cvd = QLabel("CVD: 0.0")
        layout.addWidget(self.lbl_cvd)

        self.lbl_rvol = QLabel("RVOL: 1.0x")
        layout.addWidget(self.lbl_rvol)

    def _on_source_changed(self, text: str) -> None:
        if self.on_change_source:
            self.on_change_source(text)

    def update_snapshot(self, snapshot: MarketSnapshot) -> None:
        if not HAS_PYSIDE:
            return
        self.lbl_price.setText(f"Price: {snapshot.last_price:.2f}")
        cvd_color = THEME.bull_green if snapshot.cumulative_volume_delta >= 0 else THEME.bear_red
        self.lbl_cvd.setText(f"CVD: {snapshot.cumulative_volume_delta:+.1f}")
        self.lbl_cvd.setStyleSheet(f"color: {cvd_color}; font-weight: bold;")
        self.lbl_rvol.setText(f"RVOL: {snapshot.rvol:.2f}x")
