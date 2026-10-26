from typing import Dict, List, NamedTuple, Tuple
from tmap.core.types import VolumeBar
from tmap.utils.math_helpers import safe_div


class ImbalanceLevel(NamedTuple):
    price: float
    buy_vol: float
    sell_vol: float
    ratio: float
    is_buy_imbalance: bool


def calculate_imbalances(
    bar: VolumeBar,
    ratio_threshold: float = 3.0,
    min_volume: float = 1.0,
) -> List[ImbalanceLevel]:
    """
    Calculate diagonal footprint imbalances for a VolumeBar.
    
    A buy imbalance occurs when Buy Volume at Price P >= ratio * Sell Volume at Price (P - tick).
    A sell imbalance occurs when Sell Volume at Price P >= ratio * Buy Volume at Price (P + tick).
    """
    imbalances: List[ImbalanceLevel] = []
    prices = sorted(bar.profile.keys())
    if len(prices) < 2:
        return imbalances

    for i in range(len(prices)):
        p = prices[i]
        buy_p, sell_p = bar.profile[p]

        # Check buy imbalance against lower price sell volume
        if i > 0:
            p_lower = prices[i - 1]
            sell_lower = bar.profile[p_lower][1]
            if buy_p >= min_volume and (sell_lower == 0 or (buy_p / (sell_lower + 1e-9)) >= ratio_threshold):
                ratio = safe_div(buy_p, sell_lower, fallback=999.0)
                imbalances.append(
                    ImbalanceLevel(
                        price=p,
                        buy_vol=buy_p,
                        sell_vol=sell_lower,
                        ratio=ratio,
                        is_buy_imbalance=True,
                    )
                )

        # Check sell imbalance against higher price buy volume
        if i < len(prices) - 1:
            p_upper = prices[i + 1]
            buy_upper = bar.profile[p_upper][0]
            if sell_p >= min_volume and (buy_upper == 0 or (sell_p / (buy_upper + 1e-9)) >= ratio_threshold):
                ratio = safe_div(sell_p, buy_upper, fallback=999.0)
                imbalances.append(
                    ImbalanceLevel(
                        price=p,
                        buy_vol=buy_upper,
                        sell_vol=sell_p,
                        ratio=ratio,
                        is_buy_imbalance=False,
                    )
                )

    return imbalances


def calculate_delta_percent(bar: VolumeBar) -> float:
    """Calculate net delta as a percentage of total volume [-100.0, 100.0]."""
    if bar.total_volume <= 0:
        return 0.0
    return (bar.delta / bar.total_volume) * 100.0
