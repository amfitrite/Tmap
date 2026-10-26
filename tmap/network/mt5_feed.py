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

    def _parse_line(self, line: str) -> None:
        """Parse raw CSV or delimited packet from EA."""
        if not line:
            return
        try:
            # Protocol: TICK,timestamp,price,vol,side,bid,ask
            parts = line.split(",")
            if parts[0] == "TICK" and len(parts) >= 7:
                t = float(parts[1])
                p = float(parts[2])
                v = float(parts[3])
                side_str = parts[4].upper()
                side = TradeSide.BUY if side_str == "BUY" else TradeSide.SELL
                bid = float(parts[5])
                ask = float(parts[6])

                tick = Tick(timestamp=t, price=p, volume=v, side=side, bid=bid, ask=ask)
                if self.on_tick:
                    self.on_tick(tick)
        except Exception as err:
            logger.debug(f"Failed to parse MT5 packet: '{line}', error: {err}")
