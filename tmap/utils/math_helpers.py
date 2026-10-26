import math
from typing import Union

Number = Union[int, float]


def clamp(val: Number, low: Number, high: Number) -> Number:
    """Clamp a value between lower and upper bounds."""
    return max(low, min(val, high))


def lerp(a: float, b: float, t: float) -> float:
    """Linear interpolation between a and b by factor t [0.0, 1.0]."""
    return a + (b - a) * clamp(t, 0.0, 1.0)


def safe_div(num: float, den: float, fallback: float = 0.0, eps: float = 1e-9) -> float:
    """Safely divide two numbers, avoiding ZeroDivisionError with epsilon guard."""
    if abs(den) < eps:
        return fallback
    return num / den


def round_to_tick(price: float, tick_size: float = 0.01) -> float:
    """Round a price to the nearest instrument tick step."""
    if tick_size <= 0:
        return price
    return round(price / tick_size) * tick_size


def normalize_lerp(factor: float, dt: float, baseline_dt: float = 0.01667) -> float:
    """
    Delta-time invariant LERP factor for variable frame rate (60 FPS to 120+ FPS).
    Prevents camera and visual springs from speeding up or jittering at high refresh rates.
    """
    clamped_f = clamp(factor, 0.0, 0.9999)
    if dt <= 0:
        return clamped_f
    return 1.0 - math.pow(1.0 - clamped_f, dt / baseline_dt)
