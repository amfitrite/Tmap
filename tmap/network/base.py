from abc import ABC, abstractmethod
from typing import Callable, Optional
from tmap.core.types import MarketDepth, Tick


class BaseFeed(ABC):
    """
    Abstract Base Class for real-time market data providers.
    Feeds deliver ticks and orderbook depth snapshots asynchronously.
    """

    def __init__(self) -> None:
        self.on_tick: Optional[Callable[[Tick], None]] = None
        self.on_depth: Optional[Callable[[MarketDepth], None]] = None
        self.on_error: Optional[Callable[[str], None]] = None
        self._is_running: bool = False

    @property
    def is_running(self) -> bool:
        return self._is_running

    @abstractmethod
    def start(self) -> None:
        """Start streaming data in a background worker thread."""
        pass

    @abstractmethod
    def stop(self) -> None:
        """Stop data stream and release sockets/resources."""
        pass
