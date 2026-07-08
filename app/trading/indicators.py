"""@responsibility 지표 순수함수 — EMA·SMA·ATR, 전략·차트가 소비하는 계산 전용

Pure indicator functions over Candle lists. No I/O, no state — every value
is derived from the candles passed in, so they are trivially unit-testable
and identical in live and backtest. Kept deliberately minimal: the prop
engine needs a trend EMA, an SMA and ATR (stops, Keltner squeeze), nothing
else.
"""
from __future__ import annotations

from .models import Candle


def ema(values: list[float], period: int) -> list[float]:
    """Exponential moving average, seeded with the first value. Returns a
    list aligned 1:1 with `values` (index i = EMA up to and including i)."""
    if not values:
        return []
    k = 2.0 / (period + 1)
    out = [values[0]]
    for v in values[1:]:
        out.append(v * k + out[-1] * (1 - k))
    return out


def sma(values: list[float], period: int) -> float | None:
    """Simple moving average of the last `period` values (None if too few)."""
    if len(values) < period or period <= 0:
        return None
    return sum(values[-period:]) / period


def atr(candles: list[Candle], period: int) -> float | None:
    """Wilder's Average True Range over the last `period` bars (None if too
    few). True range includes gaps (prev close), so it survives the violent
    candles the source warns about."""
    if len(candles) < period + 1:
        return None
    trs: list[float] = []
    for i in range(1, len(candles)):
        c, p = candles[i], candles[i - 1]
        trs.append(max(c.high - c.low, abs(c.high - p.close),
                       abs(c.low - p.close)))
    # Wilder smoothing over the tail
    atr_val = sum(trs[:period]) / period
    for tr in trs[period:]:
        atr_val = (atr_val * (period - 1) + tr) / period
    return atr_val
