"""@responsibility 평균회귀 전략 — SMA±Nσ 밴드 페이드 + 추세 레짐 가드, 레인지 알트용 (브레이크아웃의 반대)

Mean-reversion "band fade" — the counter-thesis to prop_breakout/vbo. Where the
breakout strategies BUY strength (a break of the prior extreme / a volatility
threshold), this FADES a stretch: when price closes far from its own mean it
bets on reversion back to that mean.

  mean = SMA(mr_mean_bars) of entry closes
  sd   = population stddev over the same window (Bollinger geometry)
  z    = (close - mean) / sd
  long  when z <= -mr_entry_sd   (stretched BELOW → expect bounce up)
  short when z >= +mr_entry_sd   (stretched ABOVE → expect fade down)

Regime guard: mean reversion dies in trends (fading a strong move = catching a
knife). When |close - HTF EMA| / EMA exceeds mr_trend_guard the market is judged
trending and the strategy stays flat. The ATR stop (atr_stop_mult) is the FSM's
1R anchor, same as the breakout strategies — only the ENTRY geometry differs, so
all three A/B on the same Phase 2 gate. Registered but NOT a default: it earns
live use on a symbol only by passing the gate. The hypothesis: the mature/liquid
alts the breakout gate rejected are range-bound, exactly where a fade may score.
"""
from __future__ import annotations

import math

from ..indicators import atr, ema
from ..models import Side, TradeSignal
from .base import TradingContext, TradingStrategy


def _mean_sd(values: list[float]) -> tuple[float, float]:
    n = len(values)
    mean = sum(values) / n
    var = sum((v - mean) ** 2 for v in values) / n
    return mean, math.sqrt(var)


class MeanRevertStrategy(TradingStrategy):
    name = "mean_revert"

    def _gates(self, ctx: TradingContext) -> dict | None:
        c = self.cfg
        htf, ef = ctx.htf_candles, ctx.entry_candles
        if len(htf) < c.donchian_htf_ema + 2:
            return None
        if len(ef) < max(c.mr_mean_bars, c.atr_period) + 2:
            return None

        closes = [x.close for x in ef]
        mean, sd = _mean_sd(closes[-c.mr_mean_bars:])
        cur = ef[-1]
        z = (cur.close - mean) / sd if sd > 0 else 0.0

        # fade the stretch: below the lower band → long, above the upper → short
        side = None
        if z <= -c.mr_entry_sd:
            side = Side.LONG
        elif z >= c.mr_entry_sd:
            side = Side.SHORT

        # regime guard: skip when the HTF is trending (don't fade a strong move)
        htf_ema = ema([x.close for x in htf], c.donchian_htf_ema)[-1]
        stretch = abs(cur.close - htf_ema) / htf_ema if htf_ema else 0.0
        trend_regime = c.mr_trend_guard > 0 and stretch > c.mr_trend_guard

        a = atr(ef, c.atr_period)
        stop = None
        if side is not None and a and a > 0:
            stop = (cur.close - a * c.atr_stop_mult if side is Side.LONG
                    else cur.close + a * c.atr_stop_mult)

        return {"side": side, "z": z, "mean": mean, "sd": sd,
                "upper": mean + c.mr_entry_sd * sd,
                "lower": mean - c.mr_entry_sd * sd,
                "htf_ema": htf_ema, "trend_regime": trend_regime,
                "atr": a, "stop": stop, "entry_ref": cur.close,
                "ready": bool(side is not None and sd > 0
                              and not trend_regime and stop is not None)}

    def evaluate(self, ctx: TradingContext) -> TradeSignal | None:
        g = self._gates(ctx)
        if not g or not g["ready"]:
            return None
        side = g["side"]
        band = g["lower"] if side is Side.LONG else g["upper"]
        return TradeSignal(
            side=side, signal_type="MEAN_REVERT", strength=60,
            stop_price=g["stop"], entry_hint=g["entry_ref"],
            detail=(f"fade z{g['z']:+.2f} {'≤' if side is Side.LONG else '≥'} "
                    f"{'−' if side is Side.LONG else '+'}{self.cfg.mr_entry_sd:g}σ "
                    f"→ mean {g['mean']:.4g} (band {band:.4g})"))

    def diagnose(self, ctx: TradingContext) -> dict | None:
        g = self._gates(ctx)
        if not g:
            return None
        side = g["side"].value if g["side"] else "–"
        gates = [
            {"key": "band", "label": f"±{self.cfg.mr_entry_sd:g}σ 밴드 이탈",
             "ok": bool(g["side"]), "info": f"z {g['z']:+.2f} → {side}"},
            {"key": "regime", "label": "레인지 레짐(추세 아님)",
             "ok": not g["trend_regime"],
             "info": "추세 관망" if g["trend_regime"] else "레인지 OK"},
            {"key": "atr", "label": "ATR 스탑 확보", "ok": bool(g["atr"]),
             "info": f"ATR {g['atr']:.4f}" if g["atr"] else "–"},
        ]
        passed = sum(1 for x in gates if x["ok"])
        return {"allowed": side, "ready": g["ready"],
                "passed": passed, "total": len(gates), "gates": gates,
                "atr": round(g["atr"], 4) if g["atr"] else None,
                "box_hi": round(g["upper"], 6), "box_lo": round(g["lower"], 6),
                "stop_preview": round(g["stop"], 6) if g["stop"] else None}
