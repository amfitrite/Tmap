from dataclasses import dataclass
from typing import Dict, Iterable, Optional, Tuple
from tmap.core.types import VolumeBar


@dataclass
class VolumeProfileResult:
    """Summary of a composite or bar volume profile."""
    total_volume: float
    poc_price: float              # Point of Control (Highest traded price level)
    poc_volume: float
    vah_price: float              # Value Area High (Upper bound of 70% volume)
    val_price: float              # Value Area Low (Lower bound of 70% volume)
    profile: Dict[float, float]   # Price -> Total Volume


def compute_volume_profile(
    bars: Iterable[VolumeBar],
    value_area_pct: float = 0.70,
) -> Optional[VolumeProfileResult]:
    """
    Compute Point of Control (POC) and Value Area (VAH / VAL) over a collection of bars.
    """
    composite: Dict[float, float] = {}
    total_vol = 0.0

    for bar in bars:
        for price, (b_vol, s_vol) in bar.profile.items():
            level_vol = b_vol + s_vol
            composite[price] = composite.get(price, 0.0) + level_vol
            total_vol += level_vol

    if not composite or total_vol <= 0:
        return None

    # Determine POC
    poc_price = max(composite.keys(), key=lambda p: composite[p])
    poc_volume = composite[poc_price]

    # Calculate Value Area (70% of total volume)
    target_va_vol = total_vol * value_area_pct
    accumulated_vol = poc_volume
    sorted_prices = sorted(composite.keys())
    poc_idx = sorted_prices.index(poc_price)

    up_idx = poc_idx + 1
    down_idx = poc_idx - 1

    while accumulated_vol < target_va_vol and (up_idx < len(sorted_prices) or down_idx >= 0):
        vol_up = composite[sorted_prices[up_idx]] if up_idx < len(sorted_prices) else -1.0
        vol_down = composite[sorted_prices[down_idx]] if down_idx >= 0 else -1.0

        if vol_up >= vol_down and up_idx < len(sorted_prices):
            accumulated_vol += vol_up
            up_idx += 1
        elif down_idx >= 0:
            accumulated_vol += vol_down
            down_idx -= 1
        else:
            break

    val_price = sorted_prices[max(0, down_idx + 1)]
    vah_price = sorted_prices[min(len(sorted_prices) - 1, up_idx - 1)]

    return VolumeProfileResult(
        total_volume=total_vol,
        poc_price=poc_price,
        poc_volume=poc_volume,
        vah_price=vah_price,
        val_price=val_price,
        profile=composite,
    )
