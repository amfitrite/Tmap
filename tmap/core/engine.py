from collections import deque
from typing import Deque, Dict, List, Optional
import time

from tmap.core.config import EngineConfig
from tmap.core.dom_cache import DOMCache, DOMCacheConfig
from tmap.core.events import EventBus, EventType
from tmap.core.types import MarketDepth, MarketSnapshot, Tick, TradeSide, VolumeBar
from tmap.utils.logger import get_logger
from tmap.utils.math_helpers import safe_div

logger = get_logger("Engine")


class OrderFlowEngine:
    """
    Tmap Core Real-Time Ingestion & Order Flow Engine.

    Key Principles:
    - O(1) ingestion complexity per tick.
    - Strictly bounded memory via collections.deque to eliminate memory leaks.
    - Decoupled event publication for UI and Analytics.
    """

    def __init__(self, config: Optional[EngineConfig] = None, event_bus: Optional[EventBus] = None) -> None:
        self.config = config or EngineConfig()
        self.event_bus = event_bus or EventBus()

        # Bounded historical buffers
        self.bars: Deque[VolumeBar] = deque(maxlen=self.config.max_buffer_bars)
        self.recent_ticks: Deque[Tick] = deque(maxlen=self.config.max_recent_ticks)

        # Active state
        self._current_bar: Optional[VolumeBar] = None
        self._bar_index: int = 0
        self._total_ticks_processed: int = 0
        self._cvd: float = 0.0
        self._last_depth: Optional[MarketDepth] = None
        self._last_price: float = 0.0

        # Synthetic L2 DOM cache rebuilt from MT5 ticks (Bookmap-style liquidity map)
        self.dom_cache: DOMCache = DOMCache(DOMCacheConfig(tick_size=self.config.tick_size))

        # Rolling window for RVOL calculation
        self._recent_bar_volumes: Deque[float] = deque(maxlen=self.config.rvol_window)

    @property
    def current_bar(self) -> Optional[VolumeBar]:
        return self._current_bar

    @property
    def cumulative_volume_delta(self) -> float:
        return self._cvd

    @property
    def total_ticks(self) -> int:
        return self._total_ticks_processed

    def process_tick(self, tick: Tick) -> None:
        """
        Ingest and process a single market trade tick in strictly O(1) time.
        """
        self._total_ticks_processed += 1
        self._last_price = tick.price
        self.recent_ticks.append(tick)

        # Feed the synthetic L2 reconstruction (tick-derived liquidity map)
        self.dom_cache.process_tick(tick)

        # Update Cumulative Volume Delta (CVD)
        delta_contribution = tick.volume if tick.side == TradeSide.BUY else -tick.volume
        self._cvd += delta_contribution

        # Initialize active bar if not present
        if self._current_bar is None:
            self._start_new_bar(tick)

        # Accumulate tick into active forming bar (O(1))
        self._current_bar.add_tick(tick, tick_size=self.config.tick_size)

        # Notify active bar update
        self.event_bus.emit(EventType.BAR_UPDATED, self._current_bar)
        self.event_bus.emit(EventType.TICK_RECEIVED, tick)

        # Check sealing condition (Ticks Per Bar accumulator)
        if self._current_bar.tick_count >= self.config.ticks_per_bar:
            self._seal_current_bar()

    def process_depth(self, depth: MarketDepth) -> None:
        """Process incoming limit order book snapshot (true L2 correction if available)."""
        self._last_depth = depth
        self.dom_cache.process_depth(depth)
        self.event_bus.emit(EventType.DEPTH_UPDATED, depth)

    def _start_new_bar(self, initial_tick: Tick) -> None:
        """Create a fresh forming bar."""
        self._bar_index += 1
        self._current_bar = VolumeBar(
            index=self._bar_index,
            start_time=initial_tick.timestamp,
            end_time=initial_tick.timestamp,
            open=initial_tick.price,
            high=initial_tick.price,
            low=initial_tick.price,
            close=initial_tick.price,
            total_volume=0.0,
            buy_volume=0.0,
            sell_volume=0.0,
            delta=0.0,
            tick_count=0,
            profile={},
            is_sealed=False,
        )

    def _seal_current_bar(self) -> None:
        """Finalize and seal the current bar, moving it to historical deque."""
        if self._current_bar is None:
            return

        self._current_bar.is_sealed = True
        self.bars.append(self._current_bar)
        self._recent_bar_volumes.append(self._current_bar.total_volume)

        # Emit sealed event
        self.event_bus.emit(EventType.BAR_SEALED, self._current_bar)

        # Open next bar placeholder
        last_close = self._current_bar.close
        last_time = self._current_bar.end_time
        self._bar_index += 1
        self._current_bar = VolumeBar(
            index=self._bar_index,
            start_time=last_time,
            end_time=last_time,
            open=last_close,
            high=last_close,
            low=last_close,
            close=last_close,
            total_volume=0.0,
            buy_volume=0.0,
            sell_volume=0.0,
            delta=0.0,
            tick_count=0,
            profile={},
            is_sealed=False,
        )

    def compute_rvol(self) -> float:
        """
        Compute Relative Volume (RVOL) against the rolling historical baseline.
        """
        if not self._recent_bar_volumes:
            return 1.0
        avg_vol = sum(self._recent_bar_volumes) / len(self._recent_bar_volumes)
        current_vol = self._current_bar.total_volume if self._current_bar else 0.0
        return safe_div(current_vol, avg_vol, fallback=1.0)

    def get_snapshot(self) -> MarketSnapshot:
        """Return a current thread-safe snapshot of the engine state."""
        best_bid = self._last_depth.best_bid if self._last_depth and self._last_depth.best_bid else self._last_price
        best_ask = self._last_depth.best_ask if self._last_depth and self._last_depth.best_ask else self._last_price

        # Prefer the reconstructed synthetic book when no true depth feed exists
        if self._last_depth is None or not (self._last_depth.bids or self._last_depth.asks):
            synthetic = self.dom_cache.get_depth()
            if synthetic.bids or synthetic.asks:
                best_bid = synthetic.best_bid or best_bid
                best_ask = synthetic.best_ask or best_ask

        return MarketSnapshot(
            symbol=self.config.symbol,
            last_price=self._last_price,
            best_bid=best_bid,
            best_ask=best_ask,
            cumulative_volume_delta=self._cvd,
            current_bar=self._current_bar,
            total_ticks_processed=self._total_ticks_processed,
            rvol=self.compute_rvol(),
            timestamp=time.time(),
        )

    def clear(self) -> None:
        """Reset internal buffers and state."""
        self.bars.clear()
        self.recent_ticks.clear()
        self._recent_bar_volumes.clear()
        self.dom_cache.clear()
        self._current_bar = None
        self._bar_index = 0
        self._total_ticks_processed = 0
        self._cvd = 0.0
