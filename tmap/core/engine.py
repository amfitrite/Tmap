from collections import deque
from threading import RLock
from typing import Deque, Dict, List, Optional
import os
import time

from tmap.core.config import EngineConfig
from tmap.core.dom_cache import DOMCache, DOMCacheConfig
from tmap.core.events import EventBus, EventType
from tmap.core.types import MarketDepth, MarketSnapshot, Tick, TradeSide, VolumeBar
from tmap.utils.logger import get_logger
from tmap.utils.math_helpers import safe_div

logger = get_logger("Engine")


def _signed_volume(tick: Tick) -> float:
    """Delta contribution of a tick. UNKNOWN-side ticks contribute zero so
    they never corrupt delta/CVD by silently counting as SELL."""
    if tick.side == TradeSide.BUY:
        return tick.volume
    if tick.side == TradeSide.SELL:
        return -tick.volume
    return 0.0


class OrderFlowEngine:
    """
    Tmap Core Real-Time Ingestion & Order Flow Engine.

    Key Principles:
    - O(1) ingestion complexity per tick.
    - Strictly bounded memory via collections.deque to eliminate memory leaks.
    - Decoupled event publication for UI and Analytics.
    - Thread-safe: feed threads may call process_tick/process_depth while the
      GUI thread reads state via properties or get_snapshot() (Prinsip Emas #1).
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
        self._bar_open_ts: float = 0.0
        self._last_cache_save_ts: float = 0.0

        # Re-entrant lock guarding all mutable state below (feed thread vs GUI thread)
        self._lock = RLock()

        # Synthetic L2 DOM cache rebuilt from MT5 ticks (Bookmap-style liquidity map)
        self.dom_cache: DOMCache = DOMCache(DOMCacheConfig(tick_size=self.config.tick_size))

        # Rolling window for RVOL calculation (running sum keeps compute_rvol O(1))
        self._recent_bar_volumes: Deque[float] = deque(maxlen=self.config.rvol_window)
        self._rvol_sum: float = 0.0

        # Persistent L2 DOM cache configuration & auto-loading
        self.cache_dir = getattr(self.config, "cache_dir", "cache")
        self.cache_file = os.path.join(self.cache_dir, f"dom_cache_{self.config.symbol}.json")
        self.candles_file = os.path.join(self.cache_dir, f"candles_{self.config.symbol}.json")
        self._load_cached_dom()
        self._load_cached_candles()

    @property
    def current_bar(self) -> Optional[VolumeBar]:
        with self._lock:
            return self._current_bar

    @property
    def cumulative_volume_delta(self) -> float:
        with self._lock:
            return self._cvd

    @property
    def total_ticks(self) -> int:
        with self._lock:
            return self._total_ticks_processed

    def process_tick(self, tick: Tick) -> None:
        """
        Ingest and process a single market trade tick in strictly O(1) time.
        Thread-safe: may be called from a background feed thread.
        """
        with self._lock:
            self._total_ticks_processed += 1
            self._last_price = tick.price
            self.recent_ticks.append(tick)

            # Feed the synthetic L2 reconstruction (tick-derived liquidity map)
            self.dom_cache.process_tick(tick)

            # Update Cumulative Volume Delta (CVD); UNKNOWN side contributes 0
            self._cvd += _signed_volume(tick)

            # Initialize active bar if not present
            if self._current_bar is None:
                self._start_new_bar(tick)

            # Accumulate tick into active forming bar (O(1))
            self._current_bar.add_tick(tick, tick_size=self.config.tick_size)

            sealed = (
                self._current_bar.tick_count >= self.config.ticks_per_bar
                or (
                    self.config.bar_seconds > 0
                    and self._bar_open_ts > 0.0
                    and (tick.timestamp - self._bar_open_ts) >= self.config.bar_seconds
                )
            )
            bar_snapshot = self._current_bar

            # Check sealing condition (tick-count OR time interval)
            if sealed:
                self._seal_current_bar()

        # Emit events outside the lock so subscriber callbacks cannot deadlock
        # the ingestion path (and can safely read engine state themselves).
        self.event_bus.emit(EventType.BAR_UPDATED, bar_snapshot)
        self.event_bus.emit(EventType.TICK_RECEIVED, tick)

        # Periodic autosave (throttled to ~10s) so a crash never loses the
        # whole session's DOM state and candle history.
        now = time.time()
        if now - self._last_cache_save_ts > 10.0:
            self._last_cache_save_ts = now
            self.save_cache()

    def process_depth(self, depth: MarketDepth) -> None:
        """Process incoming limit order book snapshot (true L2 correction if available)."""
        with self._lock:
            self._last_depth = depth
            self.dom_cache.process_depth(depth)
        self.event_bus.emit(EventType.DEPTH_UPDATED, depth)

    def _start_new_bar(self, initial_tick: Tick) -> None:
        """Create a fresh forming bar."""
        self._bar_index += 1
        self._bar_open_ts = initial_tick.timestamp
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
        """Finalize and seal the current bar, moving it to historical deque.
        Caller must hold self._lock."""
        if self._current_bar is None:
            return

        self._current_bar.is_sealed = True
        sealed_bar = self._current_bar
        self.bars.append(sealed_bar)

        # Maintain rolling RVOL baseline with running sum (O(1), evicts oldest)
        vol = sealed_bar.total_volume
        dropped = len(self._recent_bar_volumes) == self._recent_bar_volumes.maxlen
        old_vol = self._recent_bar_volumes[0] if dropped else 0.0
        self._recent_bar_volumes.append(vol)
        self._rvol_sum += vol - (old_vol if dropped else 0.0)

        # Emit sealed event
        self.event_bus.emit(EventType.BAR_SEALED, sealed_bar)

        # Open next bar placeholder
        last_close = sealed_bar.close
        last_time = sealed_bar.end_time
        self._bar_index += 1
        self._bar_open_ts = last_time
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
        O(1): maintains a running sum instead of re-scanning the window deque.
        """
        with self._lock:
            return self._compute_rvol_locked()

    def _compute_rvol_locked(self) -> float:
        if not self._recent_bar_volumes:
            return 1.0
        avg_vol = self._rvol_sum / len(self._recent_bar_volumes)
        current_vol = self._current_bar.total_volume if self._current_bar else 0.0
        return safe_div(current_vol, avg_vol, fallback=1.0)

    def get_snapshot(self) -> MarketSnapshot:
        """Return a current thread-safe snapshot of the engine state."""
        with self._lock:
            last_depth = self._last_depth
            dom_cache = self.dom_cache
            best_bid = last_depth.best_bid if last_depth and last_depth.best_bid else self._last_price
            best_ask = last_depth.best_ask if last_depth and last_depth.best_ask else self._last_price

            # Prefer the reconstructed synthetic book when no true depth feed exists
            if last_depth is None or not (last_depth.bids or last_depth.asks):
                synthetic = dom_cache.get_depth()
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
                rvol=self._compute_rvol_locked(),
                timestamp=time.time(),
            )

    def clear(self) -> None:
        """Reset internal buffers and state."""
        with self._lock:
            self.bars.clear()
            self.recent_ticks.clear()
            self._recent_bar_volumes.clear()
            self._rvol_sum = 0.0
            self.dom_cache.clear()
            self._current_bar = None
            self._bar_index = 0
            self._total_ticks_processed = 0
            self._cvd = 0.0
            self._last_depth = None
            self._last_price = 0.0

    # ------------------------------------------------------------- cache I/O

    def _load_cached_dom(self) -> None:
        """Attempt to restore previously reconstructed L2 DOM liquidity from disk."""
        if os.path.exists(self.cache_file):
            try:
                if self.dom_cache.load_from_file(self.cache_file):
                    if self.dom_cache.last_price > 0:
                        self._last_price = self.dom_cache.last_price
                    if self.dom_cache.last_bid > 0:
                        self._last_bid = self.dom_cache.last_bid
                    if self.dom_cache.last_ask > 0:
                        self._last_ask = self.dom_cache.last_ask
                    # Keep the engine's own tick counter consistent with the
                    # restored cache so status/RVOL don't double-count history.
                    self._total_ticks_processed = max(
                        self._total_ticks_processed, self.dom_cache.ticks_processed)
                    logger.info(
                        f"Restored L2 DOM cache from {self.cache_file} "
                        f"({len(self.dom_cache._bids)} bids, {len(self.dom_cache._asks)} asks)"
                    )
            except Exception as e:
                logger.debug(f"Failed to load DOM cache from {self.cache_file}: {e}")

    def save_cache(self) -> None:
        """Save current L2 DOM cache state (and sealed candles) to disk."""
        with self._lock:
            try:
                self.dom_cache.save_to_file(self.cache_file)
            except Exception as e:
                logger.debug(f"Failed to save DOM cache: {e}")
            if getattr(self.config, "persist_candles", True):
                try:
                    self._save_candles_locked()
                except Exception as e:
                    logger.debug(f"Failed to save candle cache: {e}")

    # --------------------------------------------------------- candle history

    def _bar_to_dict(self, bar: VolumeBar) -> Dict[str, object]:
        return {
            "index": bar.index,
            "start_time": bar.start_time,
            "end_time": bar.end_time,
            "open": bar.open,
            "high": bar.high,
            "low": bar.low,
            "close": bar.close,
            "total_volume": bar.total_volume,
            "buy_volume": bar.buy_volume,
            "sell_volume": bar.sell_volume,
            "delta": bar.delta,
            "tick_count": bar.tick_count,
            "profile": {str(p): v for p, v in bar.profile.items()},
            "is_sealed": bar.is_sealed,
        }

    def _save_candles_locked(self) -> None:
        """Atomically persist sealed bars so the chart can reload them next session."""
        import json, tempfile
        data = [self._bar_to_dict(b) for b in self.bars]
        dirname = os.path.dirname(os.path.abspath(self.candles_file))
        os.makedirs(dirname, exist_ok=True)
        tmp_fd, tmp_path = tempfile.mkstemp(dir=dirname, prefix="candles_", suffix=".tmp")
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
            json.dump(data, f)
        os.replace(tmp_path, self.candles_file)

    def _load_cached_candles(self) -> None:
        """Restore previously sealed candles from disk into the historical buffer."""
        if not getattr(self.config, "persist_candles", True):
            return
        if not os.path.exists(self.candles_file):
            return
        try:
            import json
            with open(self.candles_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            restored = 0
            for b in data:
                try:
                    bar = VolumeBar(
                        index=int(b["index"]),
                        start_time=float(b["start_time"]),
                        end_time=float(b["end_time"]),
                        open=float(b["open"]),
                        high=float(b["high"]),
                        low=float(b["low"]),
                        close=float(b["close"]),
                        total_volume=float(b.get("total_volume", 0.0)),
                        buy_volume=float(b.get("buy_volume", 0.0)),
                        sell_volume=float(b.get("sell_volume", 0.0)),
                        delta=float(b.get("delta", 0.0)),
                        tick_count=int(b.get("tick_count", 0)),
                        profile={float(k): list(v) for k, v in b.get("profile", {}).items()},
                        is_sealed=bool(b.get("is_sealed", True)),
                    )
                except (KeyError, TypeError, ValueError):
                    continue
                self.bars.append(bar)
                restored += 1
            if restored:
                last_idx = int(data[-1].get("index", 0)) if data else 0
                self._bar_index = max(self._bar_index, last_idx)
                last_bar = self.bars[-1]
                if last_bar.end_time > (self.dom_cache.last_ts or 0.0):
                    self._bar_open_ts = last_bar.end_time
                logger.info(f"Restored {restored} cached candles from {self.candles_file}")
        except Exception as e:
            logger.debug(f"Failed to load candle cache from {self.candles_file}: {e}")
