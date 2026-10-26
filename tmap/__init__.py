"""
Tmap - Next-Gen High-Performance Order Flow & Market Microstructure Terminal.

Architecture Overview:
----------------------
- core: Ingestion data types, event bus, and O(1) tick/bar aggregation engine.
- analytics: Order flow metrics (CVD, Imbalances, RVOL, Volume Profile).
- network: Real-time and mock market data feeds.
- ui: Modular UI contracts, customizable theme engine, and PySide6 reference shell.
- utils: Math helpers, numerical guards, and thread-safe logging.
"""

__version__ = "0.1.0"
__author__ = "Tmap Team"

from tmap.core.types import Tick, TradeSide, VolumeBar, MarketDepth
from tmap.core.engine import OrderFlowEngine
from tmap.core.config import EngineConfig

__all__ = [
    "__version__",
    "Tick",
    "TradeSide",
    "VolumeBar",
    "MarketDepth",
    "OrderFlowEngine",
    "EngineConfig",
]
