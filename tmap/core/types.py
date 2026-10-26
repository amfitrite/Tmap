from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Tuple


class TradeSide(Enum):
    BUY = "BUY"
    SELL = "SELL"
    UNKNOWN = "UNKNOWN"


@dataclass(slots=True)
class Tick:
    """Represents a single atomic executed market trade tick."""
    timestamp: float          # Unix epoch seconds (with microsecond precision)
    price: float              # Execution price
    volume: float             # Volume / contract size
    side: TradeSide           # Aggressor side (BUY or SELL)
    bid: float = 0.0          # Best Bid at tick time
    ask: float = 0.0          # Best Ask at tick time


@dataclass(slots=True)
class OrderBookLevel:
    """Represents a single price level in the limit order book."""
    price: float
    volume: float
    order_count: int = 1


@dataclass(slots=True)
class MarketDepth:
    """Snapshots of Limit Order Book (DOM) liquidity."""
    timestamp: float
    bids: Dict[float, float] = field(default_factory=dict)  # price -> volume
    asks: Dict[float, float] = field(default_factory=dict)  # price -> volume

    @property
    def best_bid(self) -> Optional[float]:
        return max(self.bids.keys()) if self.bids else None

    @property
    def best_ask(self) -> Optional[float]:
        return min(self.asks.keys()) if self.asks else None

    @property
    def spread(self) -> float:
        bid = self.best_bid
        ask = self.best_ask
        if bid is not None and ask is not None:
            return max(0.0, ask - bid)
        return 0.0


@dataclass
class VolumeBar:
    """
    Aggregated tick/time/volume bar.
    Maintains Footprint / Volume-at-Price profiles internally.
    """
    index: int
    start_time: float
    end_time: float
    open: float
    high: float
    low: float
    close: float
    total_volume: float = 0.0
    buy_volume: float = 0.0
    sell_volume: float = 0.0
    delta: float = 0.0
    tick_count: int = 0
    # price -> [buy_vol, sell_vol]
    profile: Dict[float, List[float]] = field(default_factory=dict)
    is_sealed: bool = False

    def add_tick(self, tick: Tick, tick_size: float = 0.01) -> None:
        """Accumulate a tick into this bar in O(1) time."""
        self.close = tick.price
        if tick.price > self.high:
            self.high = tick.price
        if tick.price < self.low:
            self.low = tick.price

        self.total_volume += tick.volume
        self.tick_count += 1
        self.end_time = tick.timestamp

        # Snap to tick grid
        snapped_price = round(tick.price / tick_size) * tick_size if tick_size > 0 else tick.price
        if snapped_price not in self.profile:
            self.profile[snapped_price] = [0.0, 0.0]  # [buy, sell]

        if tick.side == TradeSide.BUY:
            self.buy_volume += tick.volume
            self.delta += tick.volume
            self.profile[snapped_price][0] += tick.volume
        elif tick.side == TradeSide.SELL:
            self.sell_volume += tick.volume
            self.delta -= tick.volume
            self.profile[snapped_price][1] += tick.volume
        else:
            # UNKNOWN aggressor: count total volume only, never corrupt delta.
            pass


@dataclass
class MarketSnapshot:
    """Current real-time summary state of the engine."""
    symbol: str
    last_price: float
    best_bid: float
    best_ask: float
    cumulative_volume_delta: float
    current_bar: Optional[VolumeBar]
    total_ticks_processed: int
    rvol: float = 1.0
    timestamp: float = 0.0
