import unittest
import sys
from tmap.ui.contracts import IChartView, IControlPanel
from tmap.ui.components.base_canvas import ChartCanvas
from tmap.ui.components.control_panel import ControlPanel
from tmap.ui.theme import THEME, ThemeColors

try:
    from PySide6.QtWidgets import QApplication
    _app = QApplication.instance() or QApplication(sys.argv)
except Exception:
    _app = None


class TestUIContracts(unittest.TestCase):
    def test_chart_canvas_contract(self):
        canvas = ChartCanvas()
        self.assertIsInstance(canvas, IChartView)
        self.assertTrue(hasattr(canvas, "on_tick"))
        self.assertTrue(hasattr(canvas, "on_bar_updated"))
        self.assertTrue(hasattr(canvas, "on_bar_sealed"))
        self.assertTrue(hasattr(canvas, "on_depth_updated"))
        self.assertTrue(hasattr(canvas, "reset_view"))

    def test_control_panel_contract(self):
        panel = ControlPanel()
        self.assertIsInstance(panel, IControlPanel)
        self.assertTrue(hasattr(panel, "update_snapshot"))

    def test_theme_tokens(self):
        self.assertIsInstance(THEME, ThemeColors)
        self.assertTrue(THEME.bg_main.startswith("#"))
        self.assertTrue(THEME.bull_green.startswith("#"))
        self.assertTrue(THEME.bear_red.startswith("#"))


if __name__ == "__main__":
    unittest.main()
