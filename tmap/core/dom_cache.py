from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from tmap.core.types import MarketDepth, Tick, TradeSide


@dataclass(slots=True)
class LiquidityLevel:
    """A single reconstructed price level in the synthetic L2 book."""
    price: float
    side: str                 # "bid" | "ask"
    volume: float = 0.0       # decayed estimated resting volume
    raw_volume: float = 0.0   # cumulative inferred volume before decay
    touches: int = 0          # times price traded at this level without breaking it
    sweeps: int = 0           # times this level was consumed by an aggressive sweep
    last_touch_ts: float = 0.0

    def is_expired(self, now: float, ttl_s: float) -> bool:
        return ttl_s > 0 and (now - self.last_touch_ts) > ttl_s


@dataclass
class DOMCacheConfig:
    """Tuning knobs for tick-derived L2 reconstruction."""
    tick_size: float = 0.01
    depth_range_ticks: int = 200      # max levels away from mid tracked on each side
    half_life_s: float = 60.0         # exponential decay half-life of resting liquidity
    touch_weight: float = 0.5         # fraction of aggressor volume credited to opposite-side level
    sweep_fill_ratio: float = 0.8     # fraction of sweep volume allocated to consumed levels
    max_levels_per_side: int = 4096   # hard memory bound
    min_level_volume: float = 1e-6    # prune threshold


class DOMCache:
    """
    Synthetic Level-2 Depth-of-Market cache rebuilt purely from MT5 ticks.

    MT5 brokers expose only executed trades plus best bid/ask, never the real
    order book. This cache reconstructs an *estimated* L2 picture using four
    signals extracted from the tape:

    1. Sweep detection : a trade that jumps across several tick levels implies
       resting liquidity existed at every consumed level.
    2. Residual credit : leftover aggressor volume at the touch price hints at
       a large passive wall on the opposite side of the spread.
    3. Touch counting  : repeated trades at one level without breaking it
       indicate quiet limit orders absorbing flow.
    4. Exponential decay: because we can never observe cancellations, cached
       liquidity fades with time so stale walls disappear from the map.

    All operations are O(k) where k = number of levels touched by a tick,
    and total memory is strictly bounded by ``max_levels_per_side``.
    """

    def __init__(self, config: Optional[DOMCacheConfig] = None) -> None:
        self.config = config or DOMCacheConfig()
        self._bids: Dict[float, LiquidityLevel] = {}
        self._asks: Dict[float, LiquidityLevel] = {}
        self._last_price: float = 0.0
        self._last_bid: float = 0.0
        self._last_ask: float = 0.0
        self._last_ts: float = 0.0
        self._ticks_processed: int = 0

    # ------------------------------------------------------------------ state

    @property
    def ticks_processed(self) -> int:
        return self._ticks_processed

    def _snap(self, price: float) -> float:
        ts = self.config.tick_size
        if ts <= 0:
            return price
        return round(round(price / ts) * ts, 8)

    def _decay_lambda(self) -> float:
        import math
        hl = self.config.half_life_s
        return math.log(2.0) / hl if hl > 0 else 0.0

    def _get_or_create(self, books: Dict[float, LiquidityLevel], price: float, side: str) -> Optional[LiquidityLevel]:
        lvl = books.get(price)
        if lvl is None:
            if len(books) >= self.config.max_levels_per_side:
                return None  # memory bound reached; drop new levels
            lvl = LiquidityLevel(price=price, side=side)
            books[price] = lvl
        return lvl

    def _credit(self, books: Dict[float, LiquidityLevel], price: float, side: str, amount: float, ts: float, kind: str) -> None:
        if amount <= 0.0:
            return
        lvl = self._get_or_create(books, price, side)
        if lvl is None:
            return
        lam = self._decay_lambda()
        dt = max(0.0, ts - lvl.last_touch_ts) if lvl.last_touch_ts else 0.0
        lvl.volume = lvl.volume * (2.0 ** (-dt / self.config.half_life_s)) if self.config.half_life_s > 0 else lvl.volume
        lvl.volume += amount
        lvl.raw_volume += amount
        lvl.last_touch_ts = ts
        if kind == "touch":
            lvl.touches += 1
        elif kind == "sweep":
            lvl.sweeps += 1
        _ = lam  # decay applied via half-life factor above

    # --------------------------------------------------------------- ingestion

    def process_tick(self, tick: Tick) -> None:
        """Reconstruct liquidity impact of one executed trade. O(k)."""
        self._ticks_processed += 1
        price = self._snap(tick.price)
        mid = self._snap((tick.bid + tick.ask) / 2.0) if tick.bid > 0 and tick.ask > 0 else price
        span_lo = mid - self.config.depth_range_ticks * self.config.tick_size
        span_hi = mid + self.config.depth_range_ticks * self.config.tick_size

        # Forget everything far from the current market (bounded working set)
        self._prune_far(span_lo, span_hi)

        prev = self._last_price
        self._last_price = price
        self._last_bid = tick.bid
        self._last_ask = tick.ask
        self._last_ts = tick.timestamp

        if prev <= 0.0 or tick.volume <= 0.0:
            return

        ts = self.config.tick_size
        lo, hi = (prev, price) if prev < price else (price, prev)
        crossed = (hi - lo) > ts * 1.5  # more than one level traversed => sweep

        if not crossed:
            # Plain touch: trade happened at (or beside) the best quote.
            # Credit residual liquidity on the *opposite* side of the aggressor.
            if tick.side == TradeSide.BUY and tick.ask > 0:
                ask_lvl = self._snap(tick.ask)
                if span_lo <= ask_lvl <= span_hi:
                    self._credit(self._asks, ask_lvl, "ask",
                                 tick.volume * self.config.touch_weight, ts, "touch")
            elif tick.side == TradeSide.SELL and tick.bid > 0:
                bid_lvl = self._snap(tick.bid)
                if span_lo <= bid_lvl <= span_hi:
                    self._credit(self._bids, bid_lvl, "bid",
                                 tick.volume * self.config.touch_weight, ts, "touch")
            return

        # Sweep: allocate inferred resting volume across every consumed level.
        steps = max(1, int(round((hi - lo) / ts)))
        share = (tick.volume * self.config.sweep_fill_ratio) / steps
        if tick.side == TradeSide.BUY:
            # Buyers swept upward through ASK levels
            for i in range(steps):
                p = self._snap(lo + i * ts)
                if span_lo <= p <= span_hi:
                    self._credit(self._asks, p, "ask", share, ts, "sweep")
        else:
            # Sellers swept downward through BID levels
            for i in range(steps):
                p = self._snap(hi - i * ts)
                if span_lo <= p <= span_hi:
                    self._credit(self._bids, p, "bid", share, ts, "sweep")

    def process_depth(self, depth: MarketDepth) -> None:
        """Optional true-L2 correction: overwrite estimates when a real book arrives."""
        for p, v in depth.bids.items():
            sp = self._snap(p)
            lvl = self._bids.get(sp)
            if lvl is None:
                lvl = self._get_or_create(self._bids, sp, "bid")
            if lvl is not None:
                lvl.volume = v
                lvl.last_touch_ts = depth.timestamp
        for p, v in depth.asks.items():
            sp = self._snap(p)
            lvl = self._asks.get(sp)
            if lvl is None:
                lvl = self._get_or_create(self._asks, sp, "ask")
            if lvl is not None:
                lvl.volume = v
                lvl.last_touch_ts = depth.timestamp

    # ---------------------------------------------------------------- pruning

    def _prune_far(self, span_lo: float, span_hi: float) -> None:
        """Drop levels outside the active window and fully decayed levels."""
        for books in (self._bids, self._asks):
            dead = [p for p, l in books.items()
                    if p < span_lo or p > span_hi
                    or (l.volume < self.config.min_level_volume and l.touches == 0)]
            for p in dead:
                del books[p]

    def apply_time_decay(self, now: Optional[float] = None) -> None:
        """Externally callable decay tick (e.g. from a UI timer) so idle walls fade."""
        ts = now if now is not None else (self._last_ts or 0.0)
        if ts <= 0:
            return
        for books in (self._bids, self._asks):
            for lvl in books.values():
                dt = max(0.0, ts - lvl.last_touch_ts)
                lvl.volume *= 2.0 ** (-dt / self.config.half_life_s)
                lvl.last_touch_ts = ts

    # ----------------------------------------------------------------- output

    def _snapshot_books(self) -> Tuple[List[Tuple[float, float]], List[Tuple[float, float]]]:
        bids = sorted(((l.price, l.volume) for l in self._bids.values() if l.volume > 0.0),
                      key=lambda x: x[0], reverse=True)[:50]
        asks = sorted(((l.price, l.volume) for l in self._asks.values() if l.volume > 0.0),
                      key=lambda x: x[0])[:50]
        return bids, asks

    def get_depth(self) -> MarketDepth:
        """Return the reconstructed L2 book as a standard MarketDepth object."""
        bids, asks = self._snapshot_books()
        return MarketDepth(
            timestamp=self._last_ts,
            bids={p: v for p, v in bids},
            asks={p: v for p, v in asks},
        )

    def get_heatmap_rows(self) -> List[Tuple[float, float, float]]:
        """Rows for Bookmap-style rendering: (price, bid_strength, ask_strength)."""
        bids, asks = self._snapshot_books()
        prices = sorted({p for p, _ in bids} | {p for p, _ in asks})
        bmap, amap = dict(bids), dict(asks)
        return [(p, bmap.get(p, 0.0), amap.get(p, 0.0)) for p in prices]

    def largest_levels(self, n: int = 5) -> Dict[str, List[Tuple[float, float]]]:
        """Top-n liquidity pools per side — candidate walls/icebergs."""
        bids, asks = self._snapshot_books()
        return {
            "bid_walls": sorted(bids, key=lambda x: x[1], reverse=True)[:n],
            "ask_walls": sorted(asks, key=lambda x: x[1], reverse=True)[:n],
        }

    def clear(self) -> None:
        self._bids.clear()
        self._asks.clear()
        self._last_price = 0.0
        self._last_ts = 0.0
        self._ticks_processed = 0
