"""@responsibility HTF 지지/저항 되돌림 전략 — 추세 방향으로만 지지 반등 매수/저항 반등 매도 (브레이크아웃의 거울)

HTF support/resistance pullback — the mirror of prop_breakout. Where the
breakout strategies SELL a break BELOW the channel low and BUY a break ABOVE the
channel high, this trades the BOUNCE off those same structural levels, but only
in the direction of the HTF trend:

  support    = lowest low over the last support_lookback HTF bars (structure)
  resistance = highest high over the same window
  uptrend (HTF close >= EMA)   -> LONG a bounce off SUPPORT
  downtrend (HTF close <  EMA)  -> SHORT a rejection at RESISTANCE

A long fires when the entry bar dips INTO the support zone (low within
support_zone_atr * ATR of the level) and CLOSES back above it — a rejection /
bounce, not a break. The stop sits just beyond the level (structural
invalidation), so risk is tight and R:R beats a late breakout entry. This is
"buy the pullback WITH the trend", the opposite failure mode from mean_revert
(which faded AGAINST the trend and lost). Registered but NOT a default — it
earns live use on a symbol only by passing the Phase 2 gate. Signal only; the
FSM owns sizing, target and trailing.
"""
from __future__ import annotations

from ..indicators import atr, ema
from ..models import Side, TradeSignal
from .base import TradingContext, TradingStrategy


class HtfSupportStrategy(TradingStrategy):
    name = "htf_support"

    def _gates(self, ctx: TradingContext) -> dict | None:
        c = self.cfg
        htf, ef = ctx.htf_candles, ctx.entry_candles
        if len(htf) < max(c.donchian_htf_ema, c.support_lookback) + 2:
            return None
        if len(ef) < c.atr_period + 2:
            return None

        # structural levels from the HTF window (current HTF bar excluded)
        window = htf[-(c.support_lookback + 1):-1]
        support = min(x.low for x in window)
        resistance = max(x.high for x in window)

        htf_ema = ema([x.close for x in htf], c.donchian_htf_ema)[-1]
        allowed = Side.LONG if htf[-1].close >= htf_ema else Side.SHORT

        a = atr(ef, c.atr_period)
        cur = ef[-1]
        zone = (a or 0.0) * c.support_zone_atr
        level = support if allowed is Side.LONG else resistance
        stop = touched = held = None
        if a and a > 0:
            if allowed is Side.LONG:                 # 지지 반등 매수
                touched = cur.low <= support + zone
                held = cur.close > support           # 지지 아래로 마감하지 않음
                stop = support - a * c.atr_stop_mult
            else:                                    # 저항 반등 매도
                touched = cur.high >= resistance - zone
                held = cur.close < resistance
                stop = resistance + a * c.atr_stop_mult
        return {"allowed": allowed, "htf_ema": htf_ema, "support": support,
                "resistance": resistance, "level": level, "zone": zone,
                "touched": bool(touched), "held": bool(held), "atr": a,
                "stop": stop, "entry_ref": cur.close,
                "ready": bool(touched and held and a and a > 0
                              and stop is not None)}

    def evaluate(self, ctx: TradingContext) -> TradeSignal | None:
        g = self._gates(ctx)
        if not g or not g["ready"]:
            return None
        side = g["allowed"]
        return TradeSignal(
            side=side, signal_type="HTF_SUPPORT", strength=65,
            stop_price=g["stop"], entry_hint=g["entry_ref"],
            detail=(f"{'지지' if side is Side.LONG else '저항'} {g['level']:.4g} "
                    f"{'반등매수' if side is Side.LONG else '반등매도'} "
                    f"@ {g['entry_ref']:.4g}"))

    def diagnose(self, ctx: TradingContext) -> dict | None:
        g = self._gates(ctx)
        if not g:
            return None
        allowed = g["allowed"].value
        lab = "지지" if g["allowed"] is Side.LONG else "저항"
        gates = [
            {"key": "trend", "label": f"HTF 추세(EMA{self.cfg.donchian_htf_ema})",
             "ok": True, "info": f"{allowed} · vs {g['htf_ema']:.4g}"},
            {"key": "touch", "label": f"{lab} 존 터치",
             "ok": g["touched"], "info": f"레벨 {g['level']:.4g}"},
            {"key": "hold", "label": "레벨 지켜 마감(반등)",
             "ok": g["held"], "info": "반등" if g["held"] else "이탈"},
            {"key": "atr", "label": "ATR 스탑 확보", "ok": bool(g["atr"]),
             "info": f"ATR {g['atr']:.4f}" if g["atr"] else "–"},
        ]
        passed = sum(1 for x in gates if x["ok"])
        return {"allowed": allowed, "ready": g["ready"],
                "passed": passed, "total": len(gates), "gates": gates,
                "atr": round(g["atr"], 4) if g["atr"] else None,
                "box_hi": round(g["resistance"], 6),
                "box_lo": round(g["support"], 6),
                "stop_preview": round(g["stop"], 6) if g["stop"] else None}
