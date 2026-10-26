"""Design-token single source of truth (Fase 1 — DESIGN_PRO_VISUAL.md).

Everything visual (colors, spacing, typography, radii, elevation) lives here.
Rule enforced by tests: no widget may hardcode ``QColor("#...")`` or a raw
font family/size — always go through ``THEME`` / ``SPACING`` / ``TYPO`` /
``RADIUS`` helpers below.
"""

from dataclasses import dataclass
from typing import Dict, Tuple


def _shift(hex_color: str, lum_delta: float) -> str:
    """Shift a #RRGGBB color's luminance by +/- fraction (gradient token helper)."""
    h = hex_color.lstrip("#")
    r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    def adj(c: int) -> int:
        v = c + 255.0 * lum_delta if lum_delta >= 0 else c * (1.0 + lum_delta)
        return max(0, min(255, int(round(v))))
    return f"#{adj(r):02X}{adj(g):02X}{adj(b):02X}"


@dataclass(frozen=True)
class ThemeColors:
    """Modern dark theme design tokens for financial terminal visualization."""
    # Backgrounds
    bg_main: str = "#0D0F12"
    bg_surface: str = "#14171D"
    bg_panel: str = "#1A1E26"
    bg_card: str = "#222733"

    # Borders & Dividers
    border_subtle: str = "#2B3240"
    border_accent: str = "#3B82F6"

    # Market Colors
    bull_green: str = "#00E676"        # Aggressive buy / positive delta
    bear_red: str = "#FF3366"          # Aggressive sell / negative delta
    neutral_gray: str = "#6B7280"
    gold_highlight: str = "#FFD700"     # POC / Whale highlight
    cyan_info: str = "#00E5FF"          # CVD Line / Imbalance highlight

    # Pro candle palette (TradingView-style hollow dojis & soft wicks)
    bull_candle: str = "#26A69A"        # muted teal fill (up)
    bear_candle: str = "#EF5350"        # muted red fill (down)
    wick_bull: str = "#4DB6AC"          # lighter teal wick
    wick_bear: str = "#E57373"          # lighter red wick
    doji_stroke: str = "#B0BEC5"        # hollow doji outline
    grid_line: str = "#1C222D"          # price/time grid lines
    axis_text: str = "#8A93A6"          # right-side price scale labels

    # Heatmap Gradient Stops
    heatmap_low: str = "#0D1117"
    heatmap_mid: str = "#1E3A8A"
    heatmap_high: str = "#F59E0B"
    heatmap_ultra: str = "#EF4444"

    # Typography
    text_primary: str = "#F9FAFB"
    text_secondary: str = "#9CA3AF"
    text_muted: str = "#4B5563"

    # --- Fase 1 design tokens (DESIGN_PRO_VISUAL.md) --------------------
    # Chart gutter (price-scale strip): one step darker than the chart bg
    bg_chart_gutter: str = "#0A0C0F"
    # Candle body vertical gradient (±8% luminance, pro "lit from above" look)
    bull_candle_grad_top: str = _shift("#26A69A", +0.08)
    bull_candle_grad_bottom: str = _shift("#26A69A", -0.08)
    bear_candle_grad_top: str = _shift("#EF5350", +0.08)
    bear_candle_grad_bottom: str = _shift("#EF5350", -0.08)
    # Focus ring for accessibility outlines on interactive widgets
    focus_ring: str = "#3B82F6"
    # Badge text on gold highlights (last-price pill) — token, not literal
    badge_text: str = "#0D0F12"
    # 8-stop perceptual heatmap ramps (dim -> bright), see phase-2 plan §2.3
    heat_bid_stops: Tuple[str, ...] = (
        "#08131C", "#0A1F2E", "#0C3747", "#0E4D64",
        "#106F80", "#12A4A4", "#3FD0BE", "#7FFFD4",
    )
    heat_ask_stops: Tuple[str, ...] = (
        "#170F05", "#2E1A0A", "#4A2A0C", "#7A3B0E",
        "#B25B0B", "#F59E0B", "#FFC14D", "#FFE08A",
    )


THEME = ThemeColors()

# ---------------------------------------------------------------- spacing
# 4px-grid layout scale — every margin/spacing/padding uses these keys.
SPACING: Dict[str, int] = {"xs": 4, "sm": 8, "md": 12, "lg": 16, "xl": 24}

# ---------------------------------------------------------------- typography
# Single source of truth for fonts. ``mono`` carries tabular figures so all
# numeric columns stay vertically aligned while ticks stream in (the #1 tell
# of a "cheap" app is digits jittering horizontally each update).
TYPO: Dict[str, object] = {
    "mono": "Consolas",           # tabular by design; Cascadia Mono on Win11
    "ui": "Segoe UI",
    "size": {
        "chart_axis": 8,          # price-scale labels
        "badge": 9,               # last-price pill
        "ladder": 9,              # DOM ladder rows
        "header": 9,              # panel headers
        "metric": 13,             # toolbar metric strip (price / CVD / RVOL)
        "body": 10,               # general widget text
    },
}

# ---------------------------------------------------------------- radii
RADIUS: Dict[str, int] = {"panel": 10, "chip": 6, "menu": 8, "badge": 4}

# ---------------------------------------------------------------- elevation
# Three shadow levels for popup / menu / tooltip (rgba strings for QSS
# ``box-shadow``-style usage and QPainter drop shadows in later phases).
ELEVATION: Dict[str, str] = {
    "level1": "rgba(0, 0, 0, 0.35)",   # tooltips
    "level2": "rgba(0, 0, 0, 0.50)",   # context menus
    "level3": "rgba(0, 0, 0, 0.65)",   # modals / floating panels
}


def mono_font(size_key: str = "body", bold: bool = False):
    """Return a QFont with tabular figures from the TYPO token scale.

    Lazily imports PySide6 so this module stays usable headless/in tests.
    ``setFeature('tnum', True)`` keeps digit widths constant when PySide6 >=
    6.4 exposes OpenType features; Consolas/Cascadia are tabular regardless.
    """
    from PySide6.QtGui import QFont

    font = QFont(str(TYPO["mono"]), int(TYPO["size"][size_key]))
    if bold:
        font.setBold(True)
    try:  # OpenType tabular figures (best-effort across Qt versions)
        font.setFeature("tnum", True)
    except Exception:
        pass
    return font


def ui_font(size_key: str = "body", bold: bool = False):
    """Return a Segoe-UI family QFont sized from the TYPO token scale."""
    from PySide6.QtGui import QFont

    font = QFont(str(TYPO["ui"]), int(TYPO["size"][size_key]))
    if bold:
        font.setBold(True)
    return font

QSS_STYLESHEET = f"""
QMainWindow {{
    background-color: {THEME.bg_main};
    color: {THEME.text_primary};
    font-family: '{TYPO["ui"]}', -apple-system, BlinkMacSystemFont, Roboto, sans-serif;
}}

QWidget {{
    background-color: {THEME.bg_surface};
    color: {THEME.text_primary};
}}

QToolBar {{
    background-color: {THEME.bg_surface};
    border-bottom: 1px solid {THEME.border_subtle};
    padding: {SPACING["sm"]}px;
    spacing: {SPACING["sm"]}px;
}}

QPushButton {{
    background-color: {THEME.bg_panel};
    color: {THEME.text_primary};
    border: 1px solid {THEME.border_subtle};
    border-radius: {RADIUS["chip"]}px;
    padding: {SPACING["sm"]}px {SPACING["lg"] - 2}px;
    font-weight: 500;
}}

QPushButton:hover {{
    background-color: {THEME.bg_card};
    border-color: {THEME.border_accent};
}}

QPushButton:pressed {{
    background-color: {THEME.border_accent};
    color: #FFFFFF;
}}

QPushButton:focus {{
    outline: none;
    border: 1px solid {THEME.focus_ring};
}}

QLabel {{
    background-color: transparent;
    color: {THEME.text_primary};
}}

QSlider::groove:horizontal {{
    height: 4px;
    background: {THEME.bg_panel};
    border-radius: 2px;
}}

QSlider::sub-page:horizontal {{
    background: {THEME.border_accent};
    border-radius: 2px;
}}

QSlider::handle:horizontal {{
    background: {THEME.text_primary};
    border: 1px solid {THEME.border_accent};
    width: 14px;
    margin-top: -5px;
    margin-bottom: -5px;
    border-radius: 7px;
}}

QStatusBar {{
    background-color: {THEME.bg_surface};
    border-top: 1px solid {THEME.border_subtle};
    color: {THEME.text_secondary};
}}

/* Premium context-menu chrome (right-click settings) */
QMenu {{
    background-color: {THEME.bg_panel};
    color: {THEME.text_primary};
    border: 1px solid {THEME.border_subtle};
    border-radius: {RADIUS["menu"]}px;
    padding: {SPACING["xs"]}px;
}}

QMenu::item {{
    padding: {SPACING["xs"] + 2}px {SPACING["xl"]}px {SPACING["xs"] + 2}px {SPACING["md"]}px;
    border-radius: {RADIUS["chip"]}px;
}}

QMenu::item:selected {{
    background-color: {THEME.bg_card};
    color: {THEME.text_primary};
}}

QMenu::separator {{
    height: 1px;
    background: {THEME.border_subtle};
    margin: {SPACING["xs"]}px {SPACING["sm"]}px;
}}

QToolTip {{
    background-color: {THEME.bg_card};
    color: {THEME.text_primary};
    border: 1px solid {THEME.border_subtle};
    border-radius: {RADIUS["chip"]}px;
    padding: {SPACING["xs"]}px {SPACING["sm"]}px;
}}

/* ---- Fase 4: premium toolbar chrome (DESIGN_PRO_VISUAL.md) ---------- */
#Toolbar {{
    background-color: {THEME.bg_surface};
    border-bottom: 1px solid {THEME.border_subtle};
}}
#BrandChip {{
    font-weight: bold;
    color: {THEME.border_accent};
    background: {THEME.bg_main};
    border: 1px solid {THEME.border_subtle};
    border-radius: {RADIUS["chip"]}px;
    padding: 3px 10px;
}}
#MetricValue {{
    color: {THEME.text_secondary};
}}
#SegmentSeparator {{
    background-color: {THEME.border_subtle};
    max-width: 1px;
}}
#ProgressStrip {{
    background: transparent;
    border: none;
}}
#ProgressStrip::chunk {{
    background-color: {THEME.gold_highlight};
    border-radius: 1px;
}}

/* QPushButton hover = bg lighten ~6% + accent border, radius token-driven */
QPushButton {{
    border-radius: {RADIUS["chip"]}px;
}}
QPushButton:hover {{
    background-color: {_shift(THEME.bg_panel, +0.06)};
    border: 1px solid {THEME.border_accent};
}}
QPushButton:checked {{
    background-color: {THEME.bg_card};
    border: 1px solid {THEME.border_accent};
    color: {THEME.cyan_info};
}}

/* Taller menu items with accent checkmark (Fase 4.4) */
QMenu::item {{
    min-height: 22px;
}}
QMenu::indicator {{
    width: 14px;
    height: 14px;
}}
QMenu::indicator:checked {{
    background-color: {THEME.cyan_info};
    border: 1px solid {THEME.cyan_info};
    border-radius: 3px;
}}
QMenu::indicator:unchecked {{
    background-color: transparent;
    border: 1px solid {THEME.border_subtle};
    border-radius: 3px;
}}

/* ---- Fase 4.5: premium combo-box chrome (source selector) ---------- */
QComboBox {{
    background-color: {THEME.bg_panel};
    color: {THEME.text_primary};
    border: 1px solid {THEME.border_subtle};
    border-radius: {RADIUS["chip"]}px;
    padding: 3px {SPACING["sm"]}px;
    min-height: 20px;
}}
QComboBox:hover, QComboBox:focus, QComboBox:on {{
    border: 1px solid {THEME.border_accent};
    outline: none;
}}
QComboBox::drop-down {{
    subcontrol-origin: padding;
    subcontrol-position: top right;
    width: 18px;
    border: none;
    background: transparent;
}}
QComboBox::down-arrow {{
    image: none;
    border-left: 4px solid transparent;
    border-right: 4px solid transparent;
    border-top: 5px solid {THEME.text_secondary};
    margin-right: 6px;
}}
QComboBox QAbstractItemView {{
    background-color: {THEME.bg_panel};
    color: {THEME.text_primary};
    border: 1px solid {THEME.border_subtle};
    border-radius: {RADIUS["menu"]}px;
    padding: {SPACING["xs"]}px;
    selection-background-color: {THEME.bg_card};
    selection-color: {THEME.cyan_info};
    outline: none;
}}
"""
