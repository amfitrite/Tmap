from abc import ABC, abstractmethod
from typing import Optional, Protocol, runtime_checkable

from tmap.core.types import MarketDepth, MarketSnapshot, Tick, VolumeBar


@runtime_checkable
class IChartView(Protocol):
    """Protocol contract for the primary orderflow visual canvas."""

    def on_tick(self, tick: Tick) -> None:
        """Invoked when a new market trade occurs."""
        ...

    def on_bar_updated(self, bar: VolumeBar) -> None:
        """Invoked when the active forming bar receives updates."""
        ...

    def on_bar_sealed(self, bar: VolumeBar) -> None:
        """Invoked when an active bar reaches tick threshold and seals."""
        ...

    def on_depth_updated(self, depth: MarketDepth) -> None:
        """Invoked when DOM limit order book changes."""
        ...

    def reset_view(self) -> None:
        """Reset camera zoom and center price."""
        ...


@runtime_checkable
class IControlPanel(Protocol):
    """Protocol contract for user control bars / toolbars."""

    def update_snapshot(self, snapshot: MarketSnapshot) -> None:
        """Receive periodic or event-driven market summary updates."""
        ...
