"""Offline MT5 tick downloader & cache replayer (MetaTrader5 Python API).

MT5 brokers never expose the real order book, but they DO store full tick
history. This module lets Tmap *download real historical ticks* straight from
the terminal via the official ``MetaTrader5`` python package and replay them
through the engine so the synthetic L2 DOM cache gets populated with genuine
market microstructure instantly — a Bookmap-style limit-order map built from
real tape data instead of waiting for a live session.

Typical usage::

    from tmap.network.mt5_ticks import download_ticks, replay_ticks

    ticks = download_ticks("XAUUSD", from_date="2026-10-01", to_date="2026-10-26")
    stats = replay_ticks(ticks, engine)          # fills dom_cache + candles

The MetaTrader5 package only exists on Windows; everywhere else every entry
point degrades gracefully (``mt5_available()`` is False / clear exceptions).
"""

import os
import time
from datetime import datetime, timedelta, timezone
from typing import Callable, Iterable, List, Optional, Tuple

from tmap.core.types import Tick, TradeSide
from tmap.utils.logger import get_logger

logger = get_logger("MT5Ticks")

# Default local persistence so a downloaded session can be reloaded offline.
# Anchored to the project root (never the process CWD, which on an elevated
# Windows launch sits in C:\Windows\System32 and blocks writes with WinError 5).
DEFAULT_TICKS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "cache")


def mt5_available() -> bool:
    """True when the official MetaTrader5 python package is importable."""
    try:
        import MetaTrader5  # noqa: F401
        return True
    except ImportError:
        return False


class MT5ConnectionError(RuntimeError):
    """Raised when the MetaTrader5 terminal cannot be reached."""


def _connect(path: Optional[str] = None, login: Optional[int] = None,
             password: Optional[str] = None, server: Optional[str] = None) -> None:
    import MetaTrader5 as mt5
    kwargs = {}
    if path:
        kwargs["path"] = path
    if login is not None:
        kwargs.update(login=int(login), password=password or "", server=server or "")
    if not mt5.initialize(**kwargs):
        code, info = mt5.last_error()
        raise MT5ConnectionError(f"MetaTrader5.initialize() failed: [{code}] {info}")
    logger.info("Connected to MetaTrader 5 terminal.")


def symbol_info(symbol: str):
    """Return the MT5 symbol spec (digits, point, tick_size) or None."""
    import MetaTrader5 as mt5
    _ensure_init()
    return mt5.symbol_info(symbol)


def _ensure_init() -> None:
    import MetaTrader5 as mt5
    if mt5.terminal_info() is None:
        _connect()


def download_ticks(
    symbol: str,
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
    hours: Optional[float] = None,
    max_ticks: int = 2_000_000,
    mt5_path: Optional[str] = None,
    login: Optional[int] = None,
    password: Optional[str] = None,
    server: Optional[str] = None,
    progress_cb: Optional[Callable[[int], None]] = None,
) -> List[Tick]:
    """Download EXEC-tick history for *symbol* directly from the MT5 terminal.

    Args:
        symbol      : e.g. "XAUUSD" (must exist in the Market Watch).
        from_date   : "YYYY-MM-DD" [HH:MM:SS]" (default: 1 trading day back).
        to_date     : same format (default: now).
        hours       : convenience alternative to from_date/to_date.
        max_ticks   : hard safety cap on the number of returned ticks.
        progress_cb : called with the running count of converted ticks.

    Returns normalized :class:`Tick` objects ready for ``engine.process_tick``.
    BUY/SELL aggression is classified with the classic three-tape rule using
    the bid/ask quoted at each tick time.
    """
    import MetaTrader5 as mt5
    _connect(path=mt5_path, login=login, password=password, server=server)
    try:
        info = mt5.symbol_info(symbol)
        if info is None:
            raise MT5ConnectionError(
                f"Symbol '{symbol}' not found in MT5 Market Watch — add it first.")
        if not mt5.symbol_select(symbol, True):
            raise MT5ConnectionError(f"Failed to activate symbol '{symbol}' in Market Watch.")

        now = datetime.now(timezone.utc)
        if hours is not None:
            dt_from = now - timedelta(hours=float(hours))
        elif from_date:
            dt_from = _parse_dt(from_date)
        else:
            dt_from = now - timedelta(days=1)
        dt_to = _parse_dt(to_date) if to_date else now

        rates = mt5.copy_ticks_range(
            symbol, dt_from, dt_to, mt5.COPY_TICKS_ALL, start=0, count=max_ticks)
        if rates is None:
            err = mt5.last_error()
            logger.warning(f"copy_ticks_range returned nothing for {symbol}: {err}")
            return []

        ticks: List[Tick] = []
        append = ticks.append
        for r in rates:
            ts = float(r["time"])
            bid = float(r.get("bid", 0.0))
            ask = float(r.get("ask", 0.0))
            last = float(r.get("last", 0.0))
            vol = float(r.get("volume", 0.0))
            price = last if last > 0.0 else round((bid + ask) / 2.0, 8)
            if price <= 0.0:
                continue
            # Three-tape aggressor classification
            if ask > 0 and price >= ask - 1e-9:
                side = TradeSide.BUY
            elif bid > 0 and price <= bid + 1e-9:
                side = TradeSide.SELL
            else:
                side = TradeSide.UNKNOWN
            append(Tick(timestamp=ts, price=price,
                         volume=vol if vol > 0 else 1.0,
                         side=side, bid=bid, ask=ask))
            if progress_cb and len(ticks) % 50_000 == 0:
                progress_cb(len(ticks))
        logger.info(f"Downloaded {len(ticks)} MT5 ticks for {symbol} "
                    f"({dt_from:%Y-%m-%d %H:%M} → {dt_to:%Y-%m-%d %H:%M})")
        return ticks
    finally:
        mt5.shutdown()


def _parse_dt(value: str) -> datetime:
    value = value.strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(value, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    raise ValueError(f"Unrecognized date format: '{value}' (expected YYYY-MM-DD [HH:MM[:SS]])")


# ------------------------------------------------------------------ persistence

def save_ticks(ticks: Iterable[Tick], path: str) -> int:
    """Persist ticks as compact JSON-lines (ts|price|vol|side|bid|ask)."""
    import os
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    n = 0
    with open(path, "w", encoding="utf-8") as f:
        for t in ticks:
            f.write(f"{t.timestamp:.3f}|{t.price}|{t.volume}|{t.side.value}|{t.bid}|{t.ask}\n")
            n += 1
    return n


def load_ticks(path: str) -> List[Tick]:
    """Load ticks previously saved by :func:`save_ticks`. Empty list if missing."""
    import os
    if not os.path.exists(path):
        return []
    out: List[Tick] = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            parts = line.strip().split("|")
            if len(parts) < 6:
                continue
            try:
                side = TradeSide(parts[3])
            except ValueError:
                side = TradeSide.UNKNOWN
            try:
                out.append(Tick(timestamp=float(parts[0]), price=float(parts[1]),
                                volume=float(parts[2]), side=side,
                                bid=float(parts[4]), ask=float(parts[5])))
            except ValueError:
                continue
    return out


def default_ticks_file(symbol: str) -> str:
    import os
    return os.path.join(DEFAULT_TICKS_DIR, f"ticks_{symbol}.txt")


# ------------------------------------------------------------- synthetic ticks
# Fallback "tiruan" tape: when the MetaTrader5 package / terminal is not
# available we still generate a realistic tick stream (GBM walk + Poisson
# trades + Pareto whale blocks) so the L2 DOM cache can be pre-populated and
# the Bookmap-style limit-order view has meaningful data immediately.

def generate_synthetic_ticks(
    symbol: str = "XAUUSD",
    initial_price: float = 2750.0,
    hours_back: float = 4.0,
    tick_size: float = 0.01,
    ticks_per_second: float = 3.0,
    seed: Optional[int] = None,
) -> List[Tick]:
    """Generate a realistic historical-looking tick tape ending at ``now``."""
    import random as _random
    rng = _random.Random(seed)
    n = max(100, int(hours_back * 3600.0 * ticks_per_second))
    now = time.time()
    start_ts = now - n / ticks_per_second
    price = initial_price
    ticks: List[Tick] = []
    append = ticks.append
    vol_sigma = tick_size * 1.6
    for i in range(n):
        ts = start_ts + i / ticks_per_second
        step = rng.gauss(0.0, vol_sigma)
        price = max(tick_size, round((price + step) / tick_size) * tick_size)
        spread = round(rng.choice([1, 2, 3]) * tick_size, 4)
        bid = round(price - spread / 2.0, 4)
        ask = round(price + spread / 2.0, 4)
        side = TradeSide.BUY if rng.random() > 0.48 else TradeSide.SELL
        trade_price = ask if side == TradeSide.BUY else bid
        if rng.random() < 0.05:
            volume = round(rng.uniform(25.0, 150.0), 2)   # institutional block
        else:
            volume = round(rng.expovariate(0.5) + 0.1, 2)  # retail flow
        append(Tick(timestamp=round(ts, 3), price=trade_price, volume=volume,
                    side=side, bid=bid, ask=ask))
    logger.info(f"Generated {len(ticks)} synthetic ticks for {symbol} "
                f"(~{hours_back:g}h @ {ticks_per_second:g}/s)")
    return ticks


def get_or_generate_ticks(symbol: str, hours_back: float = 4.0,
                          force_download: bool = False, **kwargs) -> Tuple[List[Tick], str]:
    """Best-source tick acquisition used by the UI menu action.

    Priority: fresh MT5 download (if the terminal is reachable) → cached tick
    file on disk → synthetic fallback tape. Returns ``(ticks, source_label)``.
    """
    path = kwargs.pop("ticks_file", None) or default_ticks_file(symbol)
    if mt5_available():
        try:
            ticks = download_ticks(symbol, hours=hours_back, **kwargs)
            if ticks:
                save_ticks(ticks, path)
                return ticks, "MT5 (real ticks)"
            logger.warning("MT5 returned no ticks; falling back to local/synthetic tape.")
        except Exception as exc:
            logger.warning(f"MT5 tick download failed: {exc}")
    if not force_download:
        cached = load_ticks(path)
        if cached:
            logger.info(f"Loaded {len(cached)} cached ticks from {path}")
            return cached, "Local tick cache"
    ticks = generate_synthetic_ticks(symbol=symbol, hours_back=hours_back)
    try:
        save_ticks(ticks, path)
    except OSError:
        pass
    return ticks, "Synthetic (tiruan)"


# ------------------------------------------------------------------ replaying

def replay_ticks(
    ticks: List[Tick],
    engine,
    chunk_size: int = 20_000,
    pause_s: float = 0.0,
    should_stop: Optional[Callable[[], bool]] = None,
) -> Tuple[int, int]:
    """Feed historical ticks through ``engine.process_tick`` in chunks.

    Chunks keep the GUI responsive (caller runs this from a worker thread and
    shows progress between chunks). Returns ``(ticks_replayed, bars_sealed)``.
    """
    total = 0
    sealed_before = len(engine.bars)
    for i in range(0, len(ticks), chunk_size):
        if should_stop and should_stop():
            break
        for t in ticks[i:i + chunk_size]:
            engine.process_tick(t)
            total += 1
        if pause_s > 0:
            time.sleep(pause_s)
    sealed = max(0, len(engine.bars) - sealed_before)
    logger.info(f"Replayed {total} ticks ({sealed} new bars sealed)")
    return total, sealed
