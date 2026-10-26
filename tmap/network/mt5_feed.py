import json
import socket
import threading
import time
from typing import Optional

from tmap.core.types import MarketDepth, Tick, TradeSide
from tmap.network.base import BaseFeed
from tmap.utils.logger import get_logger

logger = get_logger("MT5Feed")


class MT5TCPFeed(BaseFeed):
    """
    Bridge client connecting to MetaTrader 5 Expert Advisor (EA) via local TCP socket.
    Receives raw tick and market depth packets from TmapBridge.mq5.

    Supported wire protocols (one message per line):
    1. JSON lines (documented protocol, README):
       {"type":"tick","ts":...,"price":...,"volume":...,"side":"BUY","bid":...,"ask":...}
       {"type":"depth","ts":...,"bids":{"2650.20":12.0},"asks":{"2650.30":8.0}}
    2. Legacy CSV:
       TICK,<ts>,<price>,<vol>,<side>,<bid>,<ask>
       DEPTH,<ts>,<bid_p>:<bid_v>|...,<ask_p>:<ask_v>|...
    """

    def __init__(self, host: str = "127.0.0.1", port: int = 5555, reconnect_interval: float = 3.0) -> None:
        super().__init__()
        self.host = host
        self.port = port
        self.reconnect_interval = reconnect_interval

        self._socket: Optional[socket.socket] = None
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()

    def start(self) -> None:
        if self._is_running:
            return
        self._is_running = True
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._connection_loop, name="MT5FeedWorker", daemon=True)
        self._thread.start()
        logger.info(f"MT5 TCP client started, targeting {self.host}:{self.port}")

    def stop(self) -> None:
        if not self._is_running:
            return
        self._is_running = False
        self._stop_event.set()
        if self._socket:
            try:
                self._socket.close()
            except Exception:
                pass
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1.5)
        logger.info("MT5 TCP client stopped.")

    def _connection_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                self._socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                self._socket.settimeout(2.0)
                self._socket.connect((self.host, self.port))
                logger.info(f"Connected to MT5 EA bridge on {self.host}:{self.port}")

                buffer = ""
                while not self._stop_event.is_set():
                    try:
                        data = self._socket.recv(4096).decode("utf-8")
                        if not data:
                            break
                        buffer += data
                        while "\n" in buffer:
                            line, buffer = buffer.split("\n", 1)
                            self._parse_line(line.strip())
                    except socket.timeout:
                        continue
                    except Exception as e:
                        logger.warning(f"MT5 stream read error: {e}")
                        break

            except (ConnectionRefusedError, OSError):
                time.sleep(self.reconnect_interval)
            finally:
                if self._socket:
                    try:
                        self._socket.close()
                    except Exception:
                        pass
                self._socket = None

    @staticmethod
    def _parse_side(side_str: str) -> TradeSide:
        s = (side_str or "").upper()
        if s == "BUY" or s == "B":
            return TradeSide.BUY
        if s == "SELL" or s == "S":
            return TradeSide.SELL
        return TradeSide.UNKNOWN

    def _dispatch_tick(self, tick: Tick) -> None:
        if self.on_tick:
            self.on_tick(tick)

    def _dispatch_depth(self, depth: MarketDepth) -> None:
        if self.on_depth:
            self.on_depth(depth)
        elif depth.bids or depth.asks:
            logger.debug("Received depth packet but no on_depth callback is registered; dropping.")

    def _parse_line(self, line: str) -> None:
        """Parse one packet line: JSON (documented protocol) or legacy CSV."""
        if not line:
            return
        try:
            if line[0] == "{":
                self._parse_json(line)
                return

            # Legacy CSV protocol
            parts = line.split(",")
            kind = parts[0].upper()
            if kind == "TICK" and len(parts) >= 7:
                tick = Tick(
                    timestamp=float(parts[1]),
                    price=float(parts[2]),
                    volume=float(parts[3]),
                    side=self._parse_side(parts[4]),
                    bid=float(parts[5]),
                    ask=float(parts[6]),
                )
                self._dispatch_tick(tick)
            elif kind == "DEPTH" and len(parts) >= 4:
                bids = self._parse_levels(parts[2])
                asks = self._parse_levels(parts[3])
                self._dispatch_depth(MarketDepth(timestamp=float(parts[1]), bids=bids, asks=asks))
            else:
                logger.debug(f"Unknown MT5 packet kind: '{kind}'")
        except Exception as err:
            logger.debug(f"Failed to parse MT5 packet: '{line}', error: {err}")

    @staticmethod
    def _parse_levels(spec: str) -> dict:
        """Parse 'price:vol|price:vol' level lists used by the legacy DEPTH rows."""
        levels = {}
        for chunk in spec.split("|"):
            if not chunk:
                continue
            p_str, _, v_str = chunk.partition(":")
            levels[float(p_str)] = float(v_str)
        return levels

    def _parse_json(self, line: str) -> None:
        """Parse the documented JSON-lines protocol from TmapBridge.mq5."""
        msg = json.loads(line)
        mtype = msg.get("type", "").lower()
        if mtype == "tick":
            tick = Tick(
                timestamp=float(msg["ts"]),
                price=float(msg["price"]),
                volume=float(msg.get("volume", 0.0)),
                side=self._parse_side(str(msg.get("side", ""))),
                bid=float(msg.get("bid", 0.0)),
                ask=float(msg.get("ask", 0.0)),
            )
            self._dispatch_tick(tick)
        elif mtype == "depth":
            bids = {float(p): float(v) for p, v in (msg.get("bids") or {}).items()}
            asks = {float(p): float(v) for p, v in (msg.get("asks") or {}).items()}
            self._dispatch_depth(MarketDepth(timestamp=float(msg["ts"]), bids=bids, asks=asks))
        else:
            logger.debug(f"Unknown JSON packet type: '{mtype}'")
