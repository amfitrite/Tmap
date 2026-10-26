import random
import threading
import time
from typing import Optional

from tmap.core.types import MarketDepth, Tick, TradeSide
from tmap.network.base import BaseFeed
from tmap.utils.logger import get_logger

logger = get_logger("MockFeed")


class MockMarketFeed(BaseFeed):
    """
    High-fidelity synthetic market microstructure feed generator.
    Simulates Geometric Brownian random-walk with bid-ask spread and Poisson trades.
    Ideal for development, UI design prototyping, and testing without live accounts.
    """

    def __init__(
        self,
        symbol: str = "XAUUSD",
        initial_price: float = 2750.00,
        tick_size: float = 0.01,
        tick_interval_ms: int = 15,
        depth_levels: int = 20,
    ) -> None:
        super().__init__()
        self.symbol = symbol
        self.price = initial_price
        self.tick_size = tick_size
        self.tick_interval_s = tick_interval_ms / 1000.0
        self.depth_levels = depth_levels

        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()

    def start(self) -> None:
        if self._is_running:
            return
        self._is_running = True
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run_loop, name="MockFeedWorker", daemon=True)
        self._thread.start()
        logger.info(f"Mock feed started for {self.symbol} at ${self.price:.2f}")

    def stop(self) -> None:
        if not self._is_running:
            return
        self._is_running = False
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1.0)
        logger.info("Mock feed stopped.")

    def _run_loop(self) -> None:
        depth_counter = 0

        while not self._stop_event.is_set():
            t_now = time.time()

            # Price walk: random step between -2 to +2 ticks
            step = random.choice([-2, -1, -1, 0, 0, 1, 1, 2]) * self.tick_size
            self.price = max(self.tick_size, round((self.price + step) / self.tick_size) * self.tick_size)

            spread = round(random.choice([1, 2, 3]) * self.tick_size, 4)
            best_bid = round(self.price - (spread / 2.0), 4)
            best_ask = round(self.price + (spread / 2.0), 4)

            # Trade aggressor
            side = TradeSide.BUY if random.random() > 0.48 else TradeSide.SELL
            trade_price = best_ask if side == TradeSide.BUY else best_bid

            # Pareto/Power-law volume distribution (occasional large institutional block)
            if random.random() < 0.05:
                volume = round(random.uniform(25.0, 150.0), 2)  # Whale trade
            else:
                volume = round(random.expovariate(0.5) + 0.1, 2)  # Retail / standard trade

            tick = Tick(
                timestamp=t_now,
                price=trade_price,
                volume=volume,
                side=side,
                bid=best_bid,
                ask=best_ask,
            )

            if self.on_tick:
                try:
                    self.on_tick(tick)
                except Exception as e:
                    logger.error(f"Error in on_tick callback: {e}")

            # Emit DOM orderbook depth every ~5 ticks
            depth_counter += 1
            if depth_counter >= 5:
                depth_counter = 0
                bids = {}
                asks = {}
                for lvl in range(self.depth_levels):
                    b_p = round(best_bid - (lvl * self.tick_size), 4)
                    a_p = round(best_ask + (lvl * self.tick_size), 4)
                    bids[b_p] = round(random.uniform(5.0, 80.0), 1)
                    asks[a_p] = round(random.uniform(5.0, 80.0), 1)

                depth = MarketDepth(timestamp=t_now, bids=bids, asks=asks)
                if self.on_depth:
                    try:
                        self.on_depth(depth)
                    except Exception as e:
                        logger.error(f"Error in on_depth callback: {e}")

            time.sleep(self.tick_interval_s)
