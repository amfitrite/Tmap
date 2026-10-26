from dataclasses import dataclass


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


THEME = ThemeColors()

QSS_STYLESHEET = f"""
QMainWindow {{
    background-color: {THEME.bg_main};
    color: {THEME.text_primary};
    font-family: 'Segoe UI', -apple-system, BlinkMacSystemFont, Roboto, sans-serif;
}}

QWidget {{
    background-color: {THEME.bg_surface};
    color: {THEME.text_primary};
}}

QToolBar {{
    background-color: {THEME.bg_surface};
    border-bottom: 1px solid {THEME.border_subtle};
    padding: 6px;
    spacing: 8px;
}}

QPushButton {{
    background-color: {THEME.bg_panel};
    color: {THEME.text_primary};
    border: 1px solid {THEME.border_subtle};
    border-radius: 4px;
    padding: 6px 14px;
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
"""
