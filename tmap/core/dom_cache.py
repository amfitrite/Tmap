from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from tmap.core.types import MarketDepth, Tick, TradeSide


def _decay_factor(elapsed_s: float, half_life_s: float) -> float:
    """Multiplicative decay applied to liquidity that has not been touched for
    ``elapsed_s`` seconds. Returns 1.0 when decay is disabled."""
    if half_life_s <= 0 or elapsed_s <= 0:
        return 1.0
    return 2.0 ** (-elapsed_s / half_life_s)


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
        # Cached prune window: full O(n) scans only when the market moves more
        # than one tick beyond it; otherwise removals are O(1) boundary peels.
        self._prune_lo: float = 0.0
        self._prune_hi: float = 0.0

    # ------------------------------------------------------------------ state

    @property
    def ticks_processed(self) -> int:
        return self._ticks_processed

    @property
    def last_price(self) -> float:
        return self._last_price

    @property
    def last_bid(self) -> float:
        return self._last_bid

    @property
    def last_ask(self) -> float:
        return self._last_ask

    @property
    def last_ts(self) -> float:
        return self._last_ts

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
        # Decay existing volume by the time elapsed SINCE ITS LAST TOUCH only.
        # last_touch_ts stays untouched here so apply_time_decay() cannot
        # double-count the same interval (no exponential compounding).
        dt = max(0.0, ts - lvl.last_touch_ts) if lvl.last_touch_ts else 0.0
        lvl.volume = lvl.volume * _decay_factor(dt, self.config.half_life_s) + amount
        lvl.raw_volume += amount
        lvl.last_touch_ts = ts
        if kind == "touch":
            lvl.touches += 1
        elif kind == "sweep":
            lvl.sweeps += 1

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

        # Infer aggressor side when the feed does not classify it (UNKNOWN):
        # a trade at/above the ask is buyer-initiated, at/below the bid seller.
        side = tick.side
        if side == TradeSide.UNKNOWN:
            if tick.ask > 0 and tick.price >= tick.ask:
                side = TradeSide.BUY
            elif tick.bid > 0 and tick.price <= tick.bid:
                side = TradeSide.SELL
            else:
                side = TradeSide.BUY if price > prev else (
                    TradeSide.SELL if price < prev else TradeSide.UNKNOWN
                )

        if not crossed:
            # Plain touch: trade happened at (or beside) the best quote.
            # Credit residual liquidity on the *opposite* side of the aggressor.
            if side == TradeSide.BUY and tick.ask > 0:
                ask_lvl = self._snap(tick.ask)
                if span_lo <= ask_lvl <= span_hi:
                    self._credit(self._asks, ask_lvl, "ask",
                                 tick.volume * self.config.touch_weight, ts, "touch")
            elif side == TradeSide.SELL and tick.bid > 0:
                bid_lvl = self._snap(tick.bid)
                if span_lo <= bid_lvl <= span_hi:
                    self._credit(self._bids, bid_lvl, "bid",
                                 tick.volume * self.config.touch_weight, ts, "touch")
            return

        # Sweep: allocate inferred resting volume across every consumed level.
        steps = max(1, int(round((hi - lo) / ts)))
        share = (tick.volume * self.config.sweep_fill_ratio) / steps
        if side == TradeSide.BUY:
            # Buyers swept upward through ASK levels
            for i in range(steps):
                p = self._snap(lo + i * ts)
                if span_lo <= p <= span_hi:
                    self._credit(self._asks, p, "ask", share, ts, "sweep")
        elif side == TradeSide.SELL:
            # Sellers swept downward through BID levels
            for i in range(steps):
                p = self._snap(hi - i * ts)
                if span_lo <= p <= span_hi:
                    self._credit(self._bids, p, "bid", share, ts, "sweep")
        # UNKNOWN with no inferable direction: skip directional credit entirely

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
        """Drop levels outside the active window and fully decayed levels.

        Fast path (common case): when the market moved at most one tick beyond
        the cached window, only boundary peels are needed — O(removed).
        Slow path: a large jump or first run does a single full O(n) rebuild.
        """
        ts = self.config.tick_size if self.config.tick_size > 0 else 1.0
        if self._prune_lo == 0.0 and self._prune_hi == 0.0:
            slow = True  # first call
        else:
            # Allow the window to drift by up to 2 ticks before a full rescan;
            # within that band, peel old edges incrementally instead.
            slow = (span_lo > self._prune_hi + 2 * ts) or (span_hi < self._prune_lo - 2 * ts) \
                or (span_lo < self._prune_lo - 2 * ts) or (span_hi > self._prune_hi + 2 * ts)

        if slow:
            for books in (self._bids, self._asks):
                dead = [p for p, l in books.items()
                        if p < span_lo or p > span_hi
                        or (l.volume < self.config.min_level_volume and l.touches == 0)]
                for p in dead:
                    del books[p]
            self._prune_lo, self._prune_hi = span_lo, span_hi
            return

        # Incremental peel: remove slivers that fell just outside the new window.
        for books, lo_old, hi_old in ((self._bids, self._prune_lo, self._prune_hi),
                                      (self._asks, self._prune_lo, self._prune_hi)):
            if span_lo > lo_old:
                for p in [k for k in books if k < span_lo]:
                    del books[p]
            if span_hi < hi_old:
                for p in [k for k in books if k > span_hi]:
                    del books[p]
        self._prune_lo = min(self._prune_lo, span_lo)
        self._prune_hi = max(self._prune_hi, span_hi)

    def apply_time_decay(self, now: Optional[float] = None) -> None:
        """Externally callable decay tick (e.g. from a UI timer) so idle walls fade.

        IMPORTANT: this must NOT rebaseline ``last_touch_ts`` — doing so makes
        every subsequent call re-decay the same interval, compounding the decay
        factor exponentially (walls vanish far too fast). Each level's volume
        is always derived from its absolute age: ``volume_at_touch * 2^(-age/hl)``.
        """
        ts = now if now is not None else (self._last_ts or 0.0)
        if ts <= 0:
            return
        hl = self.config.half_life_s
        if hl <= 0:
            return
        for books in (self._bids, self._asks):
            dead: List[float] = []
            for price, lvl in books.items():
                if lvl.last_touch_ts <= 0:
                    continue
                age = max(0.0, ts - lvl.last_touch_ts)
                lvl.volume *= _decay_factor(age, hl)
                if lvl.volume < self.config.min_level_volume and lvl.touches == 0:
                    dead.append(price)
            for price in dead:
                del books[price]

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

    # ------------------------------------------------------ limit-order view

    def get_limit_orders(self, min_volume: float = 0.0) -> List[LiquidityLevel]:
        """Expose reconstructed resting liquidity as a Bookmap-style DOM ladder.

        Each returned ``LiquidityLevel`` represents an *inferred* passive
        (limit) order pool sitting at that price: how much volume is estimated
        to be resting there right now (``volume``, decayed), the total volume
        ever inferred there (``raw_volume``), and how many times aggressive
        flow touched or swept through it (``touches`` / ``sweeps``).

        Sorted by strength descending so the first entries are the biggest
        limit walls. ``min_volume`` filters out dust levels.
        """
        orders = [lvl for lvl in list(self._bids.values()) + list(self._asks.values())
                  if lvl.volume > max(min_volume, self.config.min_level_volume)]
        orders.sort(key=lambda l: l.volume, reverse=True)
        return orders

    def get_ladder(self, depth: int = 25) -> Dict[str, List[LiquidityLevel]]:
        """Price-sorted DOM ladder around the market: nearest levels first.

        Returns {"bids": [...descending price...], "asks": [...ascending...]},
        mimicking the classic DOM ladder layout used by Bookmap's depth rows.
        """
        bids = sorted(self._bids.values(), key=lambda l: l.price, reverse=True)
        asks = sorted(self._asks.values(), key=lambda l: l.price)
        return {
            "bids": [l for l in bids if l.volume > self.config.min_level_volume][:depth],
            "asks": [l for l in asks if l.volume > self.config.min_level_volume][:depth],
        }

    def total_resting_liquidity(self) -> Dict[str, float]:
        """Aggregate current (decayed) resting volume per side + imbalance ratio."""
        bid_vol = sum(l.volume for l in self._bids.values())
        ask_vol = sum(l.volume for l in self._asks.values())
        denom = bid_vol + ask_vol
        return {
            "bid_volume": bid_vol,
            "ask_volume": ask_vol,
            "bid_imbalance": (bid_vol / denom) if denom > 0 else 0.5,
        }

    def clear(self) -> None:
        self._bids.clear()
        self._asks.clear()
        self._last_price = 0.0
        self._last_bid = 0.0
        self._last_ask = 0.0
        self._last_ts = 0.0
        self._ticks_processed = 0
        self._prune_lo = 0.0
        self._prune_hi = 0.0


    # ----------------------------------------------------------- persistence

    def to_dict(self) -> Dict[str, Any]:
        """Serialize reconstructed DOM cache state to dictionary."""
        return {
            "last_price": self._last_price,
            "last_bid": self._last_bid,
            "last_ask": self._last_ask,
            "last_ts": self._last_ts,
            "ticks_processed": self._ticks_processed,
            "bids": [
                {
                    "price": l.price,
                    "side": l.side,
                    "volume": l.volume,
                    "raw_volume": l.raw_volume,
                    "touches": l.touches,
                    "sweeps": l.sweeps,
                    "last_touch_ts": l.last_touch_ts,
                }
                for l in self._bids.values()
            ],
            "asks": [
                {
                    "price": l.price,
                    "side": l.side,
                    "volume": l.volume,
                    "raw_volume": l.raw_volume,
                    "touches": l.touches,
                    "sweeps": l.sweeps,
                    "last_touch_ts": l.last_touch_ts,
                }
                for l in self._asks.values()
            ],
        }

    def from_dict(self, data: Dict[str, Any], apply_decay_until: Optional[float] = None) -> None:
        """Restore DOM cache state from dictionary, with optional decay calculation."""
        self._last_price = float(data.get("last_price", 0.0))
        self._last_bid = float(data.get("last_bid", 0.0))
        self._last_ask = float(data.get("last_ask", 0.0))
        self._last_ts = float(data.get("last_ts", 0.0))
        self._ticks_processed = int(data.get("ticks_processed", 0))

        now_ts = apply_decay_until if apply_decay_until is not None else self._last_ts

        self._bids.clear()
        for b in data.get("bids", []):
            p = float(b["price"])
            last_ts = float(b.get("last_touch_ts", self._last_ts))
            elapsed = max(0.0, now_ts - last_ts)
            decay = _decay_factor(elapsed, self.config.half_life_s)
            vol = float(b.get("volume", 0.0)) * decay
            if vol > self.config.min_level_volume:
                self._bids[p] = LiquidityLevel(
                    price=p,
                    side="bid",
                    volume=vol,
                    raw_volume=float(b.get("raw_volume", vol)),
                    touches=int(b.get("touches", 0)),
                    sweeps=int(b.get("sweeps", 0)),
                    last_touch_ts=last_ts,
                )

        self._asks.clear()
        for a in data.get("asks", []):
            p = float(a["price"])
            last_ts = float(a.get("last_touch_ts", self._last_ts))
            elapsed = max(0.0, now_ts - last_ts)
            decay = _decay_factor(elapsed, self.config.half_life_s)
            vol = float(a.get("volume", 0.0)) * decay
            if vol > self.config.min_level_volume:
                self._asks[p] = LiquidityLevel(
                    price=p,
                    side="ask",
                    volume=vol,
                    raw_volume=float(a.get("raw_volume", vol)),
                    touches=int(a.get("touches", 0)),
                    sweeps=int(a.get("sweeps", 0)),
                    last_touch_ts=last_ts,
                )

    def save_to_file(self, path: str) -> None:
        """Atomically persist DOM cache state to a JSON file."""
        import json, os, tempfile
        dirname = os.path.dirname(os.path.abspath(path))
        os.makedirs(dirname, exist_ok=True)
        data = self.to_dict()
        tmp_fd, tmp_path = tempfile.mkstemp(dir=dirname, prefix="dom_", suffix=".tmp")
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp_path, path)

    def load_from_file(self, path: str, apply_decay_until: Optional[float] = None) -> bool:
        """Load DOM cache state from JSON file. Returns True if successfully loaded."""
        import json, os
        if not os.path.exists(path):
            return False
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            self.from_dict(data, apply_decay_until=apply_decay_until)
            return True
        except Exception:
            return False
