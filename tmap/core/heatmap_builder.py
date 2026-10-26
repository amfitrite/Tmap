"""Build Bookmap-style liquidity heatmap tiles from the engine's L2 DOM cache.

The engine already reconstructs synthetic limit-order (L2) liquidity from the
tape inside ``DOMCache``.  This module turns that live cache into *historical*
heatmap frames — one snapshot of resting bid/ask liquidity per time step —
and persists them through ``TileStore`` so the chart can paint a continuous
liquidity heat ribbon behind the candles, exactly like Bookmap.

Usage::

    builder = HeatmapBuilder(engine, root="data/heat_tiles")
    builder.backfill()      # freeze current cache into past frames -> tiles
    builder.attach()        # keep appending frames while ticks stream in
"""

import math
import threading
from typing import Dict, List, Optional, Tuple

from tmap.core.config import EngineConfig
from tmap.core.dom_cache import DOMCache, DOMCacheConfig
from tmap.core.heatmap_store import FrameCell, HeatFrame, TileStore, _snap_idx
from tmap.core.types import TradeSide
from tmap.utils.logger import get_logger

logger = get_logger("HeatmapBuilder")


class HeatmapBuilder:
    """Freeze DOMCache states into periodic heatmap frames and tile them."""

    # Default replay lookback for the tape-derived historical ribbon when no
    # explicit hours_back is given (ticks older than this are ignored).
    DEFAULT_REPLAY_HOURS = 8.0

    def __init__(self, engine, root: str = "data/heat_tiles",
                 frame_interval_s: float = 1.0,
                 tile_seconds: float = 60.0) -> None:
        self.engine = engine
        cfg: EngineConfig = engine.config
        self.symbol = cfg.symbol
        self.tick_size = cfg.tick_size
        self.frame_interval_s = max(0.05, float(frame_interval_s))
        self.tile_seconds = max(float(tile_seconds), self.frame_interval_s * 2)
        self.store = TileStore(root=root, tile_seconds=self.tile_seconds,
                               frame_interval_s=self.frame_interval_s)
        self._lock = threading.Lock()
        self._tile_start: Optional[float] = None
        self._frames: List[HeatFrame] = []
        self._last_frame_ts: float = 0.0
        self._pending_trades: List[Tuple[int, float, int]] = []
        self._attached = False
        # Tape-replay state: per-level touch history rebuilt from sealed bars
        self._level_first_seen: Dict[float, float] = {}
        self._level_last_touch: Dict[float, float] = {}
        self._replayed_until: float = 0.0

    # ------------------------------------------------------------- capture

    def _capture_frame(self, ts: float, mid: float, dom: DOMCache) -> None:
        """Snapshot a (decayed-to-*ts*) DOM state into one sparse heat frame."""
        cells = {}
        depth = dom.get_depth()
        for p, v in depth.bids.items():
            idx = _snap_idx(p, self.tick_size)
            cell = cells.setdefault(idx, FrameCell())
            cell.bid += v
        for p, v in depth.asks.items():
            idx = _snap_idx(p, self.tick_size)
            cell = cells.setdefault(idx, FrameCell())
            cell.ask += v
        self._frames.append(HeatFrame(timestamp=ts, mid_price=mid,
                                      cells=cells, trades=self._pending_trades))
        self._pending_trades = []
        self._last_frame_ts = ts

    def _maybe_flush_tile(self) -> None:
        if (self._tile_start is not None and self._frames
                and self._last_frame_ts - self._tile_start >= self.store.tile_seconds):
            self.store._write_tile(self.symbol, self.tick_size,
                                   self._tile_start, self._frames)
            logger.debug(f"Heatmap tile flushed at {self._tile_start:.0f}")
            self._frames = []

    # ------------------------------------------------------------- backfill

    def _collect_bars(self, hours_back: Optional[float]):
        """Sealed bars + live bar covering the requested lookback window."""
        with self.engine._lock:                                   # noqa: SLF001
            bars = list(self.engine.bars)
            live = self.engine.current_bar
            end_ts = self.engine.dom_cache.last_ts or 0.0
        if not end_ts and bars:
            end_ts = bars[-1].end_time
        if hours_back is not None and end_ts > 0:
            t0 = end_ts - float(hours_back) * 3600.0
            bars = [b for b in bars if b.end_time >= t0]
        all_bars = bars + ([live] if live is not None and live.tick_count > 0 else [])
        return all_bars, end_ts

    def backfill(self, hours_back: Optional[float] = None) -> Tuple[int, int]:
        """Rebuild the historical liquidity ribbon from recorded tape.

        For every price level we know *when it was last touched* (from the
        per-tick footprint stored inside each sealed VolumeBar). A level's
        estimated resting volume therefore existed from its first appearance
        until ``last_touch + half_life`` (after that it has decayed below the
        visible floor). We turn those intervals into additive events
        (+volume at birth, -volume at death), sweep them chronologically and
        emit one heat frame per interval — a genuine Bookmap-style heat ribbon
        derived entirely from the L2 DOM cache reconstruction.

        Returns ``(frames_written, tiles_on_disk)``.
        """
        bars, end_ts = self._collect_bars(hours_back)
        if not bars or end_ts <= 0:
            logger.warning("Heatmap backfill skipped: no recorded bars yet.")
            return (0, 0)

        with self.engine._lock:                                    # noqa: SLF001
            hl = self.engine.dom_cache.config.half_life_s
        half_life = hl if hl and hl > 0 else 120.0
        step = self.frame_interval_s
        start_ts = bars[0].start_time
        horizon = end_ts + half_life + step

        # ---- pass 1: per-level touch timeline from bar footprints ----------
        first_seen: Dict[float, float] = {}
        last_touch: Dict[float, float] = {}
        peak: Dict[float, float] = {}
        for bar in bars:
            for p, v, t, kind in self._bar_events(bar):
                if p not in first_seen or t < first_seen[p]:
                    first_seen[p] = t
                if t > last_touch.get(p, 0.0):
                    last_touch[p] = t
                peak[p] = max(peak.get(p, 0.0), v)

        # ---- pass 2: additive birth/death events ---------------------------
        # A level "dies" visually half_life seconds after its final touch;
        # levels still alive at replay end keep their current cache volume.
        with self.engine._lock:                                    # noqa: SLF001
            dom = self.engine.dom_cache
            alive_now = {**{l.price: l.volume for l in dom._bids.values()      # noqa: SLF001
                            if l.volume > 0},
                         **{l.price: l.volume for l in dom._asks.values()}}    # noqa: SLF001
        events: List[Tuple[float, int, float]] = []               # (ts, price_idx, delta)
        for p, born in first_seen.items():
            idx = _snap_idx(p, self.tick_size)
            vol = peak[p]
            events.append((born, idx, vol))
            death = min(horizon, last_touch[p] + half_life)
            if death < end_ts:                     # fully faded before 'now'
                events.append((death, idx, -vol))
            else:                                  # still alive today
                cur = alive_now.get(p, 0.0)
                if cur > 0:
                    events.append((end_ts, idx, cur - vol))

        events.sort(key=lambda e: (e[0], e[1]))

        # ---- pass 3: chronological sweep -> frames --------------------------
        active: Dict[int, float] = {}
        trades_by_bucket: Dict[int, List[Tuple[int, float, int]]] = {}
        for bar in bars:
            b = int((bar.start_time - start_ts) / step)
            side = 1 if bar.delta >= 0 else 0
            trades_by_bucket.setdefault(b, []).append(
                (_snap_idx(bar.close, self.tick_size), bar.total_volume, side))

        ei = 0
        n_frames = max(1, int(math.ceil((horizon - start_ts) / step)))
        written = 0
        with self._lock:
            self._tile_start = float(int(start_ts // self.tile_seconds) * self.tile_seconds)
            self._frames = []
            for k in range(n_frames):
                t = start_ts + k * step
                while ei < len(events) and events[ei][0] <= t:
                    _, idx, delta = events[ei]
                    nv = active.get(idx, 0.0) + delta
                    if nv > self.store.__dict__.get("_floor", 1e-9):
                        active[idx] = nv
                    else:
                        active.pop(idx, None)
                    ei += 1
                cells = {}
                for idx, v in active.items():
                    cells[idx] = FrameCell(bid=max(0.0, v), ask=0.0) \
                        if self._idx_is_bid(idx, end_ts) else \
                        FrameCell(bid=0.0, ask=max(0.0, v))
                mid = self._mid_at(t, bars)
                self._frames.append(HeatFrame(
                    timestamp=t, mid_price=mid, cells=cells,
                    trades=trades_by_bucket.get(k, [])))
                written += 1
                if (self._tile_start is not None and self._frames
                        and t - self._tile_start >= self.tile_seconds):
                    self.store._write_tile(self.symbol, self.tick_size,   # noqa: SLF001
                                           self._tile_start, self._frames)
                    self._frames = []
                    self._tile_start = float(int(t // self.tile_seconds) * self.tile_seconds)
            self.finish()
        self._replayed_until = end_ts
        tiles = len(self.store.list_tiles(self.symbol))
        logger.info(f"Heatmap backfill: {written} frames ({len(first_seen)} levels) "
                    f"-> {tiles} tiles on disk ({self.symbol})")
        return (written, tiles)

    # -------- helpers for tape-derived backfill --------
    @staticmethod
    def _bar_events(bar):
        """Yield (price, credited_volume, ts, kind) per price level of a bar.

        Uses the volume-at-price profile stored inside every VolumeBar; when a
        bar has no profile we fall back to its close so levels are never lost.
        """
        prof = getattr(bar, "profile", None) or {}
        emitted = False
        for p, vols in prof.items():
            tot = float(vols[0]) + float(vols[1]) if len(vols) >= 2 else float(vols[0])
            if tot > 0:
                yield float(p), tot, bar.end_time, "touch"
                emitted = True
        if not emitted:
            yield bar.close, max(getattr(bar, "total_volume", 0.0), 1e-9), \
                bar.end_time, "touch"

    def _idx_is_bid(self, price_idx: int, ref_ts: float) -> bool:
        """Classify a price grid index as bid-side or ask-side using the
        reconstructed book snapshot (levels above last price => ask)."""
        with self.engine._lock:                                    # noqa: SLF001
            dom = self.engine.dom_cache
            last = dom.last_price
        if last <= 0:
            return True
        p = price_idx * self.tick_size
        return p <= last + self.tick_size * 0.5

    @staticmethod
    def _mid_at(t: float, bars) -> float:
        lo, hi = 0, len(bars) - 1
        best = bars[0].close if bars else 0.0
        while lo <= hi:
            m = (lo + hi) // 2
            if bars[m].start_time <= t:
                best = bars[m].close
                lo = m + 1
            else:
                hi = m - 1
        return best

    # ------------------------------------------------------------- live feed

    def attach(self) -> None:
        """Subscribe to the engine event bus: append one frame every interval."""
        if self._attached:
            return
        from tmap.core.events import EventType
        self.engine.event_bus.subscribe(EventType.TICK_RECEIVED, self._on_tick)
        self._attached = True
        logger.info("Heatmap builder attached to live tick stream.")

    def detach(self) -> None:
        from tmap.core.events import EventType
        try:
            self.engine.event_bus.unsubscribe(EventType.TICK_RECEIVED, self._on_tick)
        except Exception:  # pragma: no cover - bus implementations vary
            pass
        self._attached = False

    def _on_tick(self, tick) -> None:
        side = 1 if getattr(tick, "side", None) == TradeSide.BUY else 0
        with self._lock:
            if self._tile_start is None:
                self._tile_start = float(int(tick.timestamp // self.store.tile_seconds)
                                         * self.store.tile_seconds)
            self._pending_trades.append(
                (_snap_idx(tick.price, self.tick_size), tick.volume, side))
            if tick.timestamp - self._last_frame_ts >= self.frame_interval_s:
                with self.engine._lock:              # noqa: SLF001 - consistent read
                    dom_view = self.engine.dom_cache
                    self._capture_frame(tick.timestamp, tick.price, dom_view)
                self._maybe_flush_tile()

    # ------------------------------------------------------------- lifecycle

    def finish(self) -> None:
        """Flush any partial tile remaining in RAM."""
        with self._lock:
            if self._frames:
                self.store._write_tile(self.symbol, self.tick_size,   # noqa: SLF001
                                       self._tile_start or self._frames[0].timestamp,
                                       self._frames)
                self._frames = []

    def query_recent(self, seconds: float, price_lo: float, price_hi: float):
        """Viewport query used by the chart overlay (time axis in seconds)."""
        with self._lock:
            t_now = max(self._last_frame_ts, self.engine.dom_cache.last_ts)
        t0 = t_now - seconds
        return self.store.query_window(self.symbol, t0, t_now, price_lo, price_hi)
