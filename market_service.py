"""Market pricing helpers for blind-box plugin."""

import random
from typing import Callable, Optional, Tuple


def clamp_volatility(value: object, low: float = 0.1, high: float = 0.5) -> float:
    try:
        v = float(value)
    except Exception:
        v = low
    return max(low, min(high, v))


def clamp_non_negative_float(value: object, default: float = 0.8) -> float:
    try:
        v = float(value)
    except Exception:
        v = default
    return max(0.0, v)


def get_daily_market_multiplier(
    *,
    date_str: str,
    category_id: str,
    volatility: float,
    kv_getter: Callable[[str], Optional[str]],
    kv_setter: Callable[[str, str], None],
) -> float:
    key = f"market_multiplier:{date_str}:{category_id}"
    stored = kv_getter(key)
    if stored is not None:
        try:
            val = float(stored)
            if val > 0:
                return val
        except Exception:
            pass

    low = max(0.01, 1.0 - volatility)
    high = 1.0 + volatility
    value = random.uniform(low, high)
    kv_setter(key, f"{value:.6f}")
    return value


def calc_scarcity_multiplier(remaining_count: int, total_count: int, scarcity_weight: float) -> float:
    if total_count <= 0:
        return 1.0
    remain = max(0, min(int(remaining_count), int(total_count)))
    scarcity_ratio = 1.0 - (remain / float(total_count))
    return 1.0 + scarcity_weight * scarcity_ratio


def calc_market_price(
    *,
    base_price: int,
    market_multiplier: float,
    scarcity_multiplier: float,
) -> int:
    if base_price <= 0:
        return 0
    raw = float(base_price) * float(market_multiplier) * float(scarcity_multiplier)
    return max(1, int(round(raw)))


def build_market_breakdown(
    *,
    base_price: int,
    market_multiplier: float,
    scarcity_multiplier: float,
) -> Tuple[int, str]:
    final_price = calc_market_price(
        base_price=base_price,
        market_multiplier=market_multiplier,
        scarcity_multiplier=scarcity_multiplier,
    )
    detail = (
        f"基准价 {base_price} × 市场系数 {market_multiplier:.3f} × 稀缺系数 {scarcity_multiplier:.3f}"
        if base_price > 0
        else "基准价待定"
    )
    return final_price, detail
