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


def ema_flip_count(values: list[float], period: int, window: int) -> int | None:
    """How many times the value flipped sides against its EMA over the last
    `window` values (None if too few). A trending series flips rarely; a
    range-bound (박스권) series whipsaws across its mean — high counts mark
    the chop regime where breakout entries bleed."""
    if window < 2 or len(values) < window:
        return None
    e = ema(values, period)
    sides = [1 if v >= m else -1 for v, m in zip(values[-window:], e[-window:])]
    return sum(1 for a, b in zip(sides, sides[1:]) if a != b)


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


def adx(candles: list[Candle], period: int) -> float | None:
    """Wilder's Average Directional Index — trend STRENGTH, not direction.
    A low ADX (~<20-25) marks a range where breakout entries whipsaw; a high
    ADX confirms a directional regime worth breaking into. Complements the
    EMA-flip chop count (which measures oscillation, not push). None until
    enough bars for the double Wilder smoothing (±DM/TR over `period`, then
    DX over `period`): needs >= 2*period+1 candles.

    Direction-agnostic on purpose — the HTF EMA filter already sets the side;
    ADX only vetoes entries when no side is trending (invariant: provider/
    regime reads never choose Long vs Short)."""
    if period <= 0 or len(candles) < 2 * period + 1:
        return None
    plus_dm: list[float] = []
    minus_dm: list[float] = []
    trs: list[float] = []
    for i in range(1, len(candles)):
        c, p = candles[i], candles[i - 1]
        up = c.high - p.high
        down = p.low - c.low
        plus_dm.append(up if up > down and up > 0 else 0.0)
        minus_dm.append(down if down > up and down > 0 else 0.0)
        trs.append(max(c.high - c.low, abs(c.high - p.close),
                       abs(c.low - p.close)))

    def _smooth(xs: list[float]) -> list[float]:
        """Wilder running total: seed = sum of first `period`, then
        s = s - s/period + x (the smoothing the DI/ADX formula assumes)."""
        s = sum(xs[:period])
        out = [s]
        for x in xs[period:]:
            s = s - s / period + x
            out.append(s)
        return out

    tr_s, pdm_s, mdm_s = _smooth(trs), _smooth(plus_dm), _smooth(minus_dm)
    dxs: list[float] = []
    for tr, pdm, mdm in zip(tr_s, pdm_s, mdm_s):
        if tr <= 0:
            dxs.append(0.0)
            continue
        pdi, mdi = 100.0 * pdm / tr, 100.0 * mdm / tr
        denom = pdi + mdi
        dxs.append(100.0 * abs(pdi - mdi) / denom if denom > 0 else 0.0)
    if len(dxs) < period:
        return None
    adx_val = sum(dxs[:period]) / period
    for dx in dxs[period:]:
        adx_val = (adx_val * (period - 1) + dx) / period
    return adx_val
