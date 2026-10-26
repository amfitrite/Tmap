"""Premium-platform style right-click context menu for the chart canvas.

Provides a QMenu with:
 - Candle interval (timeframe) submenu: 1s, 5s, 15s, 30s, 1m ... 4h
   (aggregates the engine's base bars on the UI side; no data loss)
 - Chart type, overlays (grid / price line), DOM decay half-life presets,
   zoom controls and view reset.

The module degrades gracefully when PySide6 is absent (HAS_PYSIDE = False).
"""

from typing import Callable, List, Optional, Tuple

try:
    from PySide6.QtGui import QAction, QActionGroup
    from PySide6.QtWidgets import QMenu
    HAS_PYSIDE = True
except ImportError:
    HAS_PYSIDE = False


# ---------------------------------------------------------------------------
# Timeframe catalogue: (label, seconds). Base engine bars are aggregated to
# any multiple of the base sealing period; non-multiples fall back to the
# nearest larger multiple so candles never overlap ambiguously.
# ---------------------------------------------------------------------------
TIMEFRAMES: List[Tuple[str, int]] = [
    ("1s", 1),
    ("5s", 5),
    ("15s", 15),
    ("30s", 30),
    ("1m", 60),
    ("3m", 180),
    ("5m", 300),
    ("15m", 900),
    ("30m", 1800),
    ("1h", 3600),
    ("2h", 7200),
    ("4h", 14400),
]

CHART_TYPES: List[str] = ["Candles", "Bars (OHLC)", "Line"]

DECAY_PRESETS: List[Tuple[str, float]] = [
    ("Fast fade — 30 s", 30.0),
    ("Normal — 60 s", 60.0),
    ("Slow fade — 2 min", 120.0),
    ("Persistent — 5 min", 300.0),
    ("No decay (raw)", 0.0),
]


class ChartContextMenu(QMenu if HAS_PYSIDE else object):
    """Right-click settings menu for ChartCanvas.

    Callbacks (all optional):
      on_timeframe(seconds)   : switch candle aggregation interval
      on_chart_type(name)     : Candles / Bars / Line
      on_toggle_grid(bool)
      on_toggle_price_line(bool)
      on_decay_halflife_changed(seconds)
      on_zoom_in() / on_zoom_out() / on_reset_view()
      on_load_history(hours) : download MT5 tick history (or synthetic
                               fallback) and replay it into the L2 DOM cache
    """

    # History load presets shown in the "📥 Load Limit-Order History" submenu
    LOAD_PRESETS: List[Tuple[str, float]] = [
        ("1 hour", 1.0),
        ("4 hours", 4.0),
        ("8 hours", 8.0),
        ("1 day", 24.0),
        ("3 days", 72.0),
    ]

    def __init__(
        self,
        parent=None,
        *,
        current_tf_seconds: int = 0,
        chart_type: str = "Candles",
        grid_visible: bool = True,
        price_line_visible: bool = True,
        heatmap_visible: bool = True,
        auto_scale_visible: bool = True,
        decay_halflife: float = 60.0,
        on_timeframe: Optional[Callable[[int], None]] = None,
        on_chart_type: Optional[Callable[[str], None]] = None,
        on_toggle_grid: Optional[Callable[[bool], None]] = None,
        on_toggle_price_line: Optional[Callable[[bool], None]] = None,
        on_toggle_heatmap: Optional[Callable[[bool], None]] = None,
        on_toggle_auto_scale: Optional[Callable[[bool], None]] = None,
        on_decay_halflife_changed: Optional[Callable[[float], None]] = None,
        on_zoom_in: Optional[Callable[[], None]] = None,
        on_zoom_out: Optional[Callable[[], None]] = None,
        on_reset_view: Optional[Callable[[], None]] = None,
        on_load_history: Optional[Callable[[float], None]] = None,
    ) -> None:
        if not HAS_PYSIDE:
            raise RuntimeError("PySide6 is required for ChartContextMenu.")
        super().__init__("Chart Settings", parent)

        self._cbs: dict = {
            "tf": on_timeframe,
            "chart_type": on_chart_type,
            "grid": on_toggle_grid,
            "price_line": on_toggle_price_line,
            "heatmap": on_toggle_heatmap,
            "auto_scale": on_toggle_auto_scale,
            "decay": on_decay_halflife_changed,
            "zoom_in": on_zoom_in,
            "zoom_out": on_zoom_out,
            "reset": on_reset_view,
            "load_history": on_load_history,
        }

        # ---- Load limit-order history (MT5 tick download / synthetic) ------
        load_menu = self.addMenu("📥  Load Limit-Order History")
        for label, hours in ChartContextMenu.LOAD_PRESETS:
            act = QAction(label, self)
            act.triggered.connect(lambda _checked, h=hours: self._emit("load_history", h))
            load_menu.addAction(act)
        load_menu.addSeparator()
        act_mtf = QAction("⬇  Force fresh MT5 download…", self)
        act_mtf.triggered.connect(lambda: self._emit("load_history", -1.0))
        load_menu.addAction(act_mtf)

        self.addSeparator()

        # ---- Candle Interval submenu -------------------------------------
        tf_menu = self.addMenu("⏱  Candle Interval")
        tf_group = QActionGroup(self)
        tf_group.setExclusive(True)
        self._tf_actions: dict = {}
        for label, secs in TIMEFRAMES:
            act = QAction(label, self, checkable=True)
            act.setData(secs)
            act.setChecked(secs == current_tf_seconds)
            act.triggered.connect(lambda _checked, s=secs: self._emit("tf", s))
            tf_group.addAction(act)
            tf_menu.addAction(act)
            self._tf_actions[secs] = act

        # ---- Chart Type submenu ------------------------------------------
        ct_menu = self.addMenu("📊  Chart Type")
        ct_group = QActionGroup(self)
        ct_group.setExclusive(True)
        for name in CHART_TYPES:
            act = QAction(name, self, checkable=True)
            act.setChecked(name == chart_type)
            act.triggered.connect(lambda _checked, n=name: self._emit("chart_type", n))
            ct_group.addAction(act)
            ct_menu.addAction(act)

        self.addSeparator()

        # ---- Overlays ------------------------------------------------------
        self.act_grid = QAction("Show Grid", self, checkable=True)
        self.act_grid.setChecked(grid_visible)
        self.act_grid.toggled.connect(lambda on: self._emit("grid", on))
        self.addAction(self.act_grid)

        self.act_price = QAction("Show Last Price Line", self, checkable=True)
        self.act_price.setChecked(price_line_visible)
        self.act_price.toggled.connect(lambda on: self._emit("price_line", on))
        self.addAction(self.act_price)

        self.act_heat = QAction("🔥  Show Liquidity Heatmap", self, checkable=True)
        self.act_heat.setChecked(heatmap_visible)
        self.act_heat.toggled.connect(lambda on: self._emit("heatmap", on))
        self.addAction(self.act_heat)

        self.act_auto = QAction("📐  Auto Fit Price Scale", self, checkable=True)
        self.act_auto.setChecked(auto_scale_visible)
        self.act_auto.setToolTip("Candles always fill the chart proportionally "
                                 "(like pro platforms). Ctrl+wheel zoom disables it.")
        self.act_auto.toggled.connect(lambda on: self._emit("auto_scale", on))
        self.addAction(self.act_auto)

        # ---- DOM liquidity decay (limit-order persistence) ----------------
        decay_menu = self.addMenu("🧱  DOM Liquidity Decay")
        decay_group = QActionGroup(self)
        decay_group.setExclusive(True)
        for label, hl in DECAY_PRESETS:
            act = QAction(label, self, checkable=True)
            act.setData(hl)
            act.setChecked(abs(hl - decay_halflife) < 1e-9 or (hl == 0.0 and decay_halflife <= 0.0))
            act.triggered.connect(lambda _checked, v=hl: self._emit("decay", v))
            decay_group.addAction(act)
            decay_menu.addAction(act)

        self.addSeparator()

        # ---- View controls --------------------------------------------------
        z_in = QAction("🔍  Zoom In", self)
        z_in.triggered.connect(lambda: self._emit("zoom_in"))
        self.addAction(z_in)
        z_out = QAction("🔎  Zoom Out", self)
        z_out.triggered.connect(lambda: self._emit("zoom_out"))
        self.addAction(z_out)
        rst = QAction("⟲  Reset View", self)
        rst.triggered.connect(lambda: self._emit("reset"))
        self.addAction(rst)

    # ------------------------------------------------------------------
    def set_timeframe_checked(self, seconds: int) -> None:
        """Sync the checked radio item after an external timeframe change."""
        act = self._tf_actions.get(seconds)
        if act is not None:
            act.setChecked(True)

    def _emit(self, key: str, arg=None) -> None:
        cb = self._cbs.get(key)
        if cb is None:
            return
        cb() if arg is None else cb(arg)
