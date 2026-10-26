"""
Tmap Heatmap Tile Store — "Google Maps" for liquidity.

The live DOMCache only knows the *present*: resting liquidity decays and old
price levels get pruned, so scrolling back in time would show an empty map.
This module solves that by recording a persistent, immutable history:

    ticks  ->  frames (DOMCache snapshots at fixed intervals)
         ->  tiles  (compressed column chunks stored on disk)
         ->  window queries (seamless scroll across tile boundaries)

Design principles (mirrors slippy-map tiling):
- Time is divided into fixed-size tiles (default 60s). Each tile holds one
  frame per tick_size price grid line with bid/ask strength + trade markers.
- Tiles are written once, never mutated => cheap to store, safe to cache,
  easy to stream over a network later.
- Memory is bounded: only the tile currently being recorded is held in RAM;
  older tiles live on disk (zlib-compressed binary columns).
- Queries return *sparse* frames: empty prices cost nothing, so browsing an
  hour of history stays fast and light.

File format per tile (``tiles/<symbol>/t_<start_ts>.tmht``, zlib-compressed):
    magic "TMHT" | version u8 | symbol_len u16 | start_ts f64 | end_ts f64 |
    symbol bytes | tick_size f32 | grid_len u32 | price_grid i64*grid_len |
    n_frames u32 | per frame: ts f64, mid f64, n_cells u32,
    (cell_pos u32, bid f32, ask f32)*, n_trades u32,
    (trade_pos u32, volume f32, side u8)*
"""

from __future__ import annotations

import os
import struct
import zlib
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from tmap.core.dom_cache import DOMCache
from tmap.core.types import Tick, TradeSide

_MAGIC = b"TMHT"
_VERSION = 1


@dataclass(slots=True)
class FrameCell:
    """One price column of one heatmap frame."""
    bid: float = 0.0   # resting bid liquidity estimate
    ask: float = 0.0   # resting ask liquidity estimate


@dataclass(slots=True)
class HeatFrame:
    """Immutable snapshot of the liquidity map at one moment in time."""
    timestamp: float
    mid_price: float
    cells: Dict[int, FrameCell] = field(default_factory=dict)   # price_idx -> cell
    trades: List[Tuple[int, float, int]] = field(default_factory=list)  # (price_idx, volume, 1=buy/0=sell)

    def total_strength(self) -> float:
        return sum(c.bid + c.ask for c in self.cells.values())


@dataclass(slots=True)
class PriceWindow:
    """A horizontal price band query result, aligned to the tick grid."""
    lo_idx: int
    hi_idx: int
    tick_size: float

    @property
    def lo(self) -> float:
        return self.lo_idx * self.tick_size

    @property
    def hi(self) -> float:
        return self.hi_idx * self.tick_size


@dataclass
class TileMeta:
    path: str
    symbol: str
    start_ts: float
    end_ts: float
    tick_size: float
    n_frames: int
    size_bytes: int

    @property
    def duration_s(self) -> float:
        return max(0.0, self.end_ts - self.start_ts)


def _snap_idx(price: float, tick_size: float) -> int:
    """Quantize a price to an integer grid index (exact under float rounding)."""
    return int(round(price / tick_size))


class TileStore:
    """
    Persistent tile-based heatmap archive with Google-Maps-style windowed reads.

    Usage:
        store = TileStore(root="data/tiles", tile_seconds=60)
        session = store.open_session("XAUUSD", tick_size=0.01)
        for tick in stream:
            session.ingest(tick)          # feeds internal DOMCache + records frames
        store.close()                     # flushes the active tile to disk

        # Later / from another process: replay history
        tiles = store.list_tiles("XAUUSD")
        frames = store.query_window("XAUUSD", t0, t1, price_lo, price_hi)
    """

    def __init__(self, root: str, tile_seconds: float = 60.0,
                 frame_interval_s: float = 0.5, retention_hours: float = 0.0) -> None:
        self.root = os.path.abspath(root)
        self.tile_seconds = float(tile_seconds)
        self.frame_interval_s = float(frame_interval_s)
        # 0 = keep forever; otherwise drop tiles older than this when listing/opening
        self.retention_hours = float(retention_hours)
        os.makedirs(self.root, exist_ok=True)

    # ------------------------------------------------------------------ paths

    def symbol_dir(self, symbol: str) -> str:
        d = os.path.join(self.root, symbol.replace(os.sep, "_"))
        os.makedirs(d, exist_ok=True)
        return d

    def _tile_path(self, symbol: str, start_ts: float) -> str:
        # Tiles live in a per-symbol directory and are named by zero-padded
        # integer epoch-seconds of their aligned boundary — collision-free
        # across symbols and stable under lexicographic sorting.
        return os.path.join(self.symbol_dir(symbol), f"t_{int(start_ts):013d}.tmht")

    def _unique_tile_path(self, symbol: str, start_ts: float) -> str:
        """Never silently overwrite an existing tile (e.g. two sessions that
        realign to the same boundary after a DST shift): bump until free."""
        path = self._tile_path(symbol, start_ts)
        if not os.path.exists(path):
            return path
        base, ext = os.path.splitext(path)
        n = 1
        while os.path.exists(f"{base}#{n}{ext}"):
            n += 1
        return f"{base}#{n}{ext}"

    # -------------------------------------------------------------- tile scan

    def list_tiles(self, symbol: str, now: Optional[float] = None) -> List[TileMeta]:
        """Return all persisted tiles for a symbol, oldest first."""
        metas: List[TileMeta] = []
        d = self.symbol_dir(symbol)
        for name in sorted(os.listdir(d)):
            if not name.endswith(".tmht"):
                continue
            meta = self.read_tile_meta(os.path.join(d, name))
            if meta is not None:
                metas.append(meta)
        if self.retention_hours > 0 and now:
            cutoff = now - self.retention_hours * 3600.0
            metas = [m for m in metas if m.end_ts >= cutoff]
        return metas

    # ------------------------------------------------------------ encode/decode

    @staticmethod
    def decode_tile(path: str) -> Tuple[str, float, float, float, List[HeatFrame]]:
        with open(path, "rb") as fh:
            raw = zlib.decompress(fh.read())
        if raw[:4] != _MAGIC or raw[4] != _VERSION:
            raise ValueError(f"bad tile file: {path}")
        off = 5
        (slen,) = struct.unpack_from("<H", raw, off); off += 2
        (start_ts, end_ts) = struct.unpack_from("<dd", raw, off); off += 16
        symbol = raw[off:off + slen].decode("utf-8"); off += slen
        (tick_size, n_grid) = struct.unpack_from("<fI", raw, off); off += 8
        price_indices = list(struct.unpack_from(f"<{n_grid}q", raw, off)); off += 8 * n_grid
        (n_frames,) = struct.unpack_from("<I", raw, off); off += 4
        frames: List[HeatFrame] = []
        for _ in range(n_frames):
            ts, mid, ncells = struct.unpack_from("<ddI", raw, off); off += 20
            cells: Dict[int, FrameCell] = {}
            for _c in range(ncells):
                posi, bid, ask = struct.unpack_from("<Iff", raw, off); off += 12
                cells[price_indices[posi]] = FrameCell(bid=bid, ask=ask)
            (ntr,) = struct.unpack_from("<I", raw, off); off += 4
            trades: List[Tuple[int, float, int]] = []
            for _t in range(ntr):
                posi, vol, side = struct.unpack_from("<IfB", raw, off); off += 9
                trades.append((price_indices[posi], vol, side))
            frames.append(HeatFrame(timestamp=ts, mid_price=mid, cells=cells, trades=trades))
        return symbol, tick_size, start_ts, end_ts, frames

    @staticmethod
    def read_tile_meta(path: str) -> Optional[TileMeta]:
        try:
            with open(path, "rb") as fh:
                blob = fh.read()
            raw = zlib.decompress(blob)
        except Exception:
            return None
        if raw[:4] != _MAGIC or raw[4] != _VERSION:
            return None
        off = 5
        (slen,) = struct.unpack_from("<H", raw, off); off += 2
        (start_ts, end_ts) = struct.unpack_from("<dd", raw, off); off += 16
        symbol = raw[off:off + slen].decode("utf-8"); off += slen
        (tick_size, n_grid) = struct.unpack_from("<fI", raw, off); off += 8
        off += 8 * n_grid
        (n_frames,) = struct.unpack_from("<I", raw, off)
        return TileMeta(path=path, symbol=symbol, start_ts=start_ts, end_ts=end_ts,
                        tick_size=tick_size, n_frames=n_frames, size_bytes=len(blob))

    # ---------------------------------------------------------------- encoder

    @staticmethod
    def encode(symbol: str, tick_size: float, start_ts: float, end_ts: float,
               frames: List[HeatFrame]) -> bytes:
        idx_set: set = set()
        for f in frames:
            idx_set.update(f.cells.keys())
            idx_set.update(p for p, _, _ in f.trades)
        price_indices = sorted(idx_set)
        pos_of = {pi: i for i, pi in enumerate(price_indices)}

        body = bytearray()
        for f in frames:
            body += struct.pack("<ddI", f.timestamp, f.mid_price, len(f.cells))
            for pi, cell in sorted(f.cells.items()):
                body += struct.pack("<Iff", pos_of[pi], cell.bid, cell.ask)
            body += struct.pack("<I", len(f.trades))
            for pi, vol, side in f.trades:
                body += struct.pack("<IfB", pos_of[pi], vol, side)

        sym = symbol.encode("utf-8")
        head = bytearray()
        head += _MAGIC + bytes([_VERSION])
        head += struct.pack("<Hdd", len(sym), start_ts, end_ts)
        head += sym
        head += struct.pack("<fI", tick_size, len(price_indices))
        head += struct.pack(f"<{len(price_indices)}q", *price_indices) if price_indices else b""
        head += struct.pack("<I", len(frames))
        return zlib.compress(bytes(head) + bytes(body), level=6)

    # ------------------------------------------------------------- write path

    def _write_tile(self, symbol: str, tick_size: float, start_ts: float,
                    frames: List[HeatFrame]) -> TileMeta:
        end_ts = frames[-1].timestamp if frames else start_ts
        blob = self.encode(symbol, tick_size, start_ts, end_ts, frames)
        path = self._unique_tile_path(symbol, start_ts)
        tmp = path + ".tmp"
        with open(tmp, "wb") as fh:
            fh.write(blob)
        os.replace(tmp, path)  # atomic publish: readers never see half-written tiles
        return TileMeta(path=path, symbol=symbol, start_ts=start_ts, end_ts=end_ts,
                        tick_size=tick_size, n_frames=len(frames), size_bytes=len(blob))

    # --------------------------------------------------------------- sessions

    def open_session(self, symbol: str, tick_size: float,
                     dom_config=None) -> "RecordingSession":
        return RecordingSession(self, symbol, tick_size, dom_config)

    def close(self) -> None:
        pass  # sessions flush themselves; kept for API symmetry

    # ------------------------------------------------------------- read path

    def tiles_covering(self, symbol: str, t0: float, t1: float) -> List[TileMeta]:
        return [m for m in self.list_tiles(symbol) if m.end_ts >= t0 and m.start_ts <= t1]

    def query_window(self, symbol: str, t0: float, t1: float,
                     price_lo: float, price_hi: float) -> List[HeatFrame]:
        """
        Google-Maps-style viewport query: frames intersecting [t0,t1] x [plo,phi].

        Sparse storage means out-of-window cells are simply skipped; tiles load
        only when their time range overlaps the requested window.
        """
        out: List[HeatFrame] = []
        for meta in self.tiles_covering(symbol, t0, t1):
            _, tick_size, _, _, frames = self.decode_tile(meta.path)
            plo_idx = _snap_idx(price_lo, tick_size)
            phi_idx = _snap_idx(price_hi, tick_size)
            for f in frames:
                if f.timestamp < t0 or f.timestamp > t1:
                    continue
                cells = {pi: c for pi, c in f.cells.items() if plo_idx <= pi <= phi_idx}
                trades = [tr for tr in f.trades if plo_idx <= tr[0] <= phi_idx]
                if cells or trades:
                    out.append(HeatFrame(timestamp=f.timestamp, mid_price=f.mid_price,
                                         cells=cells, trades=trades))
        out.sort(key=lambda f: f.timestamp)
        return out

    def coverage(self, symbol: str) -> List[Tuple[float, float]]:
        """Merged contiguous time ranges available on disk — the 'map extent'."""
        spans = [(m.start_ts, m.end_ts) for m in self.list_tiles(symbol)]
        merged: List[Tuple[float, float]] = []
        for s, e in sorted(spans):
            if merged and s <= merged[-1][1] + self.tile_seconds * 1.5:
                merged[-1] = (merged[-1][0], max(merged[-1][1], e))
            else:
                merged.append((s, e))
        return merged


class RecordingSession:
    """
    Live recording pipeline: ticks -> DOMCache -> periodic frames -> tiles.

    One session owns exactly one in-RAM tile at a time (bounded memory). When
    wall-clock crosses a tile boundary the finished tile is flushed to disk.
    """

    def __init__(self, store: TileStore, symbol: str, tick_size: float,
                 dom_config=None) -> None:
        self.store = store
        self.symbol = symbol
        self.tick_size = tick_size
        if dom_config is not None:
            dom_config.tick_size = tick_size
            self.dom = DOMCache(dom_config)
        else:
            from tmap.core.dom_cache import DOMCacheConfig
            self.dom = DOMCache(DOMCacheConfig(tick_size=tick_size))

        self._tile_start: Optional[float] = None
        self._frames: List[HeatFrame] = []
        self._last_frame_ts: float = 0.0
        self._pending_trades: List[Tuple[int, float, int]] = []
        self._last_mid: float = 0.0
        self.flushed_tiles: List[TileMeta] = []

    # ------------------------------------------------------------------ ingest

    def ingest(self, tick: Tick) -> None:
        """Feed one tick. O(1) amortized; may trigger a frame capture/flush."""
        self.dom.process_tick(tick)

        mid = (tick.bid + tick.ask) / 2.0 if tick.bid > 0 and tick.ask > 0 else tick.price
        self._last_mid = mid

        side = 1 if tick.side == TradeSide.BUY else 0
        self._pending_trades.append((_snap_idx(tick.price, self.tick_size), tick.volume, side))

        ts = tick.timestamp
        if self._tile_start is None:
            self._tile_start = self._align(ts)

        if ts - self._tile_start >= self.store.tile_seconds:
            self._capture_frame(ts)
            self._flush_tile()
            self._tile_start = self._align(ts)
        elif ts - self._last_frame_ts >= self.store.frame_interval_s:
            self._capture_frame(ts)

    def _align(self, ts: float) -> float:
        return float(int(ts // self.store.tile_seconds) * self.store.tile_seconds)

    def _capture_frame(self, ts: float) -> None:
        """Freeze current DOMCache state into one sparse heatmap frame."""
        cells: Dict[int, FrameCell] = {}
        depth = self.dom.get_depth()
        for p, v in depth.bids.items():
            idx = _snap_idx(p, self.tick_size)
            cell = cells.setdefault(idx, FrameCell())
            cell.bid += v
        for p, v in depth.asks.items():
            idx = _snap_idx(p, self.tick_size)
            cell = cells.setdefault(idx, FrameCell())
            cell.ask += v
        frame = HeatFrame(timestamp=ts, mid_price=self._last_mid,
                          cells=cells, trades=self._pending_trades)
        self._frames.append(frame)
        self._pending_trades = []
        self._last_frame_ts = ts

    def _flush_tile(self) -> None:
        if not self._frames or self._tile_start is None:
            return
        meta = self.store._write_tile(self.symbol, self.tick_size,
                                      self._tile_start, self._frames)
        self.flushed_tiles.append(meta)
        self._frames = []

    def finish(self) -> List[TileMeta]:
        """Flush whatever remains (partial final tile). Returns all tiles written."""
        if self._frames:
            self._flush_tile()
        return self.flushed_tiles
