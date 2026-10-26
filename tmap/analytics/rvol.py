from typing import Iterable, Sequence
from tmap.core.types import VolumeBar
from tmap.utils.math_helpers import safe_div


def calculate_rvol(current_volume: float, historical_volumes: Sequence[float]) -> float:
    """
    Calculate Relative Volume (RVOL) compared to historical baseline mean.
    Returns 1.0 if history is empty.
    """
    if not historical_volumes:
        return 1.0
    mean_vol = sum(historical_volumes) / len(historical_volumes)
    return safe_div(current_volume, mean_vol, fallback=1.0)


def classify_volume_intensity(rvol: float) -> str:
    """
    Classify RVOL into readable trading regimes:
    - ULTRA_HIGH: > 3.0x (Institutional rush / News breakout)
    - HIGH: 1.8x - 3.0x (Strong active participation)
    - NORMAL: 0.8x - 1.8x (Equilibrium range)
    - LOW: < 0.8x (Dormant / Low liquidity)
    """
    if rvol >= 3.0:
        return "ULTRA_HIGH"
    elif rvol >= 1.8:
        return "HIGH"
    elif rvol >= 0.8:
        return "NORMAL"
    else:
        return "LOW"
