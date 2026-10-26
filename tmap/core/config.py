import json
import math
import os
from dataclasses import asdict, dataclass
from typing import Any, Dict


@dataclass
class EngineConfig:
    """Core settings for data accumulation, buffering, and calculations."""
    symbol: str = "XAUUSD"
    tick_size: float = 0.01
    ticks_per_bar: int = 100            # Sealing threshold per bar
    bar_seconds: float = 0.0            # Optional time-based sealing (0 = disabled)
    max_buffer_bars: int = 5000         # Bounded memory: max bars retained in RAM
    max_recent_ticks: int = 10000       # Bounded raw tick ring buffer
    imbalance_ratio: float = 3.0        # Diagonal footprint imbalance ratio (e.g. 3x = 300%)
    imbalance_min_volume: float = 1.0   # Minimum contract threshold for imbalance highlighting
    rvol_window: int = 20               # Rolling window of bars for RVOL baseline
    heatmap_resolution_ticks: int = 1   # Grouping tick size multiplier for DOM heatmap
    storage_cache_limit_mb: int = 1024  # Max disk cache in MB
    cache_dir: str = "cache"            # Directory for persisted DOM/candle state
    persist_candles: bool = True        # Also persist sealed bars (candles) across sessions

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "EngineConfig":
        valid_keys = {f for f in cls.__dataclass_fields__}
        filtered = {k: v for k, v in data.items() if k in valid_keys}
        return cls(**filtered)

    @classmethod
    def load(cls, file_path: str) -> "EngineConfig":
        if os.path.exists(file_path):
            with open(file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                return cls.from_dict(data)
        return cls()

    def save(self, file_path: str) -> None:
        os.makedirs(os.path.dirname(os.path.abspath(file_path)), exist_ok=True)
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(asdict(self), f, indent=4)
