"""Fase 1 design-token enforcement tests (DESIGN_PRO_VISUAL.md §1).

Guards the "one design language" rules:
 * no hardcoded hex colors / raw QFont literals in UI widgets — THEME only;
 * token scales exist and are consistent (4-px grid, radii, elevation);
 * heatmap ramps have exactly 8 perceptual stops per side;
 * candle gradient tokens are derived ±8% luminance from base colors.
"""

import re
from pathlib import Path

import pytest

from tmap.ui.theme import ELEVATION, RADIUS, SPACING, THEME, TYPO, _shift

UI_DIR = Path(__file__).resolve().parent.parent / "tmap" / "ui"
WIDGET_FILES = [
    UI_DIR / "app.py",
    UI_DIR / "components" / "base_canvas.py",
    UI_DIR / "components" / "dom_panel.py",
    UI_DIR / "components" / "control_panel.py",
    UI_DIR / "components" / "status_bar.py",
    UI_DIR / "components" / "context_menu.py",
]


@pytest.mark.parametrize("path", WIDGET_FILES, ids=lambda p: p.name)
def test_no_hardcoded_hex_colors_outside_theme(path):
    """Rule §1.1: grep QColor("#...") across ui/ must be zero (theme.py exempt)."""
    src = path.read_text(encoding="utf-8")
    hits = re.findall(r'QColor\(\s*["\']#[0-9A-Fa-f]{3,8}["\']', src)
    assert not hits, f"{path.name}: hardcoded QColor hex found: {hits}"


@pytest.mark.parametrize("path", WIDGET_FILES, ids=lambda p: p.name)
def test_no_raw_qfont_literals_outside_theme(path):
    """Rule §1.2: fonts come from mono_font()/ui_font() TYPO tokens only."""
    src = path.read_text(encoding="utf-8")
    hits = re.findall(r'QFont\(\s*["\'][^"\']+["\']\s*,', src)
    assert not hits, f"{path.name}: raw QFont literal found: {hits}"


def test_spacing_scale_is_4px_grid():
    assert all(v % 4 == 0 for v in SPACING.values())
    assert SPACING == {"xs": 4, "sm": 8, "md": 12, "lg": 16, "xl": 24}


def test_typography_tokens_complete():
    assert TYPO["mono"] and TYPO["ui"]
    needed = {"chart_axis", "badge", "ladder", "header", "body"}
    assert needed <= set(TYPO["size"])


def test_radius_and_elevation_tokens():
    assert RADIUS["panel"] > RADIUS["menu"] >= RADIUS["chip"] >= RADIUS["badge"]
    assert len(ELEVATION) == 3


def test_heatmap_ramps_are_8_stops_perceptual():
    for name, stops in (("bid", THEME.heat_bid_stops), ("ask", THEME.heat_ask_stops)):
        assert len(stops) == 8, f"{name} ramp must have 8 stops"
        for c in stops:
            assert re.fullmatch(r"#[0-9A-Fa-f]{6}", c)
        # monotonic brightness: dim -> bright
        lum = [int(c[1:3], 16) + int(c[3:5], 16) + int(c[5:7], 16) for c in stops]
        assert lum == sorted(lum), f"{name} ramp not ordered dim->bright"


def test_candle_gradient_tokens_shift_luminance():
    """Gradient top is lighter, bottom darker than the base candle color."""
    def lum(h):
        return int(h[1:3], 16) + int(h[3:5], 16) + int(h[5:7], 16)
    assert lum(THEME.bull_candle_grad_top) > lum(THEME.bull_candle)
    assert lum(THEME.bull_candle_grad_bottom) < lum(THEME.bull_candle)
    assert lum(THEME.bear_candle_grad_top) > lum(THEME.bear_candle)
    assert lum(THEME.bear_candle_grad_bottom) < lum(THEME.bear_candle)


def test_shift_helper_clamps():
    assert _shift("#FFFFFF", 0.5) == "#FFFFFF"   # clamped at 255
    assert _shift("#000000", -0.5) == "#000000"   # clamped at 0
    assert _shift("#808080", 0.0) == "#808080"


def test_gutter_token_darker_than_chart_bg():
    def lum(h):
        return int(h[1:3], 16) + int(h[3:5], 16) + int(h[5:7], 16)
    assert lum(THEME.bg_chart_gutter) < lum(THEME.bg_main)
