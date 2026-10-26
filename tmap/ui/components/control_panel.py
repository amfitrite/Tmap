from typing import Callable, Optional

try:
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import (QComboBox, QFrame, QHBoxLayout, QLabel,
                                   QPushButton, QSlider, QVBoxLayout, QWidget)
    HAS_PYSIDE = True
except ImportError:
    HAS_PYSIDE = False
    QWidget = object

from tmap.core.types import MarketSnapshot
from tmap.ui.theme import SPACING, THEME, mono_font


def compute_rvol_color(rvol: float, bull: str, bear: str, neutral: str,
                       hi: float = 1.5, lo: float = 0.75) -> str:
    """Pure helper (Fase 4.1): RVOL anomaly coloring — >hi× green, <lo× red,
    otherwise neutral gray. Kept Qt-free for headless unit tests."""
    if rvol > hi:
        return bull
    if rvol < lo:
        return bear
    return neutral


class ControlPanel(QWidget if HAS_PYSIDE else object):
    """
    Top navigation and parameter adjustments toolbar (Fase 4 "premium chrome").
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
        # Fase 4.1: two-row premium toolbar — row 1 brand + actions,
        # row 2 segmented metric strip with hairline separators.
        self.setObjectName("Toolbar")  # styled via QSS token chrome
        outer = QVBoxLayout(self)
        outer.setContentsMargins(SPACING["md"], SPACING["sm"],
                                 SPACING["md"], SPACING["sm"])
        outer.setSpacing(SPACING["xs"])

        row1 = QHBoxLayout()
        row1.setSpacing(SPACING["md"])

        # Title / Brand chip (objectName styled via QSS tokens; updated via set_brand)
        self.lbl_brand = QLabel("TMAP")
        self.lbl_brand.setObjectName("BrandChip")
        self.lbl_brand.setFont(mono_font("header", bold=True))
        row1.addWidget(self.lbl_brand)

        # Feed Source Selector (MT5 Bridge vs Mock Feed)
        self.lbl_src = QLabel("Source:")
        self.lbl_src.setStyleSheet(f"color: {THEME.text_secondary};")
        row1.addWidget(self.lbl_src)

        self.combo_source = QComboBox()
        self.combo_source.addItems(["MT5 Bridge (5555)", "Synthetic Mock Feed"])
        if self._initial_source in ["MT5 Bridge (5555)", "Synthetic Mock Feed"]:
            self.combo_source.setCurrentText(self._initial_source)
        self.combo_source.currentTextChanged.connect(self._on_source_changed)
        row1.addWidget(self.combo_source)

        # Feed toggle button
        self.btn_feed = QPushButton("⏸ Pause Feed")
        if self.on_toggle_feed:
            self.btn_feed.clicked.connect(self.on_toggle_feed)
        row1.addWidget(self.btn_feed)

        # Reset camera button
        self.btn_center = QPushButton("🎯 Center Price")
        if self.on_reset_view:
            self.btn_center.clicked.connect(self.on_reset_view)
        row1.addWidget(self.btn_center)

        row1.addStretch()
        outer.addLayout(row1)

        # ---- Row 2: segmented metric strip -------------------------------
        row2 = QHBoxLayout()
        row2.setSpacing(SPACING["lg"])

        self.lbl_price = QLabel("Price: --")
        self.lbl_price.setFont(mono_font("metric", bold=True))
        self.lbl_price.setStyleSheet(f"color: {THEME.text_primary};")
        row2.addWidget(self.lbl_price)
        row2.addWidget(self._separator())

        self.lbl_cvd = QLabel("CVD: 0.0")
        self.lbl_cvd.setFont(mono_font("metric"))
        row2.addWidget(self.lbl_cvd)
        row2.addWidget(self._separator())

        self.lbl_rvol = QLabel("RVOL: 1.0x")
        self.lbl_rvol.setFont(mono_font("metric"))
        row2.addWidget(self.lbl_rvol)
        row2.addWidget(self._separator())

        # L2 imbalance readout (fed by the app's metrics tick)
        self.lbl_imb = QLabel("Imb: --")
        self.lbl_imb.setFont(mono_font("metric"))
        self.lbl_imb.setObjectName("MetricValue")
        row2.addWidget(self.lbl_imb)

        row2.addStretch()
        outer.addLayout(row2)

    @staticmethod
    def _separator() -> QFrame:
        """Vertical hairline between metric segments (Fase 4.1)."""
        line = QFrame()
        line.setFrameShape(QFrame.VLine)
        line.setStyleSheet(f"color: {THEME.border_subtle}; max-width: 1px;")
        return line

    def _on_source_changed(self, text: str) -> None:
        if self.on_change_source:
            self.on_change_source(text)

    def set_brand(self, symbol: str, tf: Optional[str] = None) -> None:
        """Update the minimal brand chip (e.g. 'TMAP · XAUUSD · 5m')."""
        parts = ["TMAP", symbol] + ([tf] if tf else [])
        self.lbl_brand.setText(" · ".join(parts))

    def set_imbalance(self, bid_imbalance: float) -> None:
        """Show L2 resting-liquidity imbalance (bid share %) with a color hint."""
        pct = bid_imbalance * 100.0
        col = THEME.bull_green if pct >= 55 else (
            THEME.bear_red if pct <= 45 else THEME.text_secondary)
        self.lbl_imb.setText(f"Imb: {pct:.0f}%B")
        self.lbl_imb.setStyleSheet(f"color: {col};")

    def update_snapshot(self, snapshot: MarketSnapshot) -> None:
        if not HAS_PYSIDE:
            return
        self.lbl_price.setText(f"Price: {snapshot.last_price:.2f}")
        cvd_color = THEME.bull_green if snapshot.cumulative_volume_delta >= 0 else THEME.bear_red
        self.lbl_cvd.setText(f"CVD: {snapshot.cumulative_volume_delta:+.1f}")
        self.lbl_cvd.setStyleSheet(f"color: {cvd_color}; font-weight: bold;")
        self.lbl_rvol.setText(f"RVOL: {snapshot.rvol:.2f}x")
        # Fase 4.1: RVOL anomaly highlight (green >1.5×, red <0.75×)
        self.lbl_rvol.setStyleSheet(
            f"color: {compute_rvol_color(snapshot.rvol, THEME.bull_green, THEME.bear_red, THEME.text_secondary)};")
