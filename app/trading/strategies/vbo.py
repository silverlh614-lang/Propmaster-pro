"""@responsibility 변동성 돌파 전략 — 래리 윌리엄스 K-룰(전기 범위×K 돌파) + HTF EMA 추세 필터

Volatility-breakout (Larry Williams K-rule), the crypto-native breakout
the ORB literature does NOT transplant to 24h markets. Where prop_breakout
waits for a break of the prior N-bar EXTREME (Donchian), this fires on a
break of a VOLATILITY THRESHOLD off the current bar's open:

  range   = high-low span over the previous vbo_range_bars bars (a "session")
  long    = current close > current open + vbo_k * range   (HTF trend up)
  short   = current close < current open - vbo_k * range   (HTF trend down)

Same HTF EMA direction filter and ATR stop as prop_breakout — only the
trigger geometry differs, so the two can be A/B'd head-to-head on the same
backtest gate. Registered but NOT the default: it earns live use only by
passing the gate on a symbol (Donchian's structural weakness on choppy alts
is exactly where a volatility trigger might do better).
"""
from __future__ import annotations

from ..indicators import atr, ema
from ..models import Side, TradeSignal
from .base import TradingContext, TradingStrategy
from .filters import confirmation_gates, confirmation_rows


class VolatilityBreakoutStrategy(TradingStrategy):
    name = "vbo"

    def _gates(self, ctx: TradingContext) -> dict | None:
        c = self.cfg
        htf, ef = ctx.htf_candles, ctx.entry_candles
        if len(htf) < c.donchian_htf_ema + 2:
            return None
        if len(ef) < max(c.vbo_range_bars, c.atr_period) + 2:
            return None

        htf_ema = ema([x.close for x in htf], c.donchian_htf_ema)[-1]
        allowed = Side.LONG if htf[-1].close >= htf_ema else Side.SHORT

        window = ef[-(c.vbo_range_bars + 1):-1]      # prior "session", current excluded
        rng = max(x.high for x in window) - min(x.low for x in window)
        cur = ef[-1]
        long_lvl = cur.open + c.vbo_k * rng
        short_lvl = cur.open - c.vbo_k * rng
        level = long_lvl if allowed is Side.LONG else short_lvl
        broke = (cur.close > long_lvl if allowed is Side.LONG
                 else cur.close < short_lvl)

        a = atr(ef, c.atr_period)
        stop = None
        if a and a > 0:
            stop = (cur.close - a * c.atr_stop_mult if allowed is Side.LONG
                    else cur.close + a * c.atr_stop_mult)

        conf = confirmation_gates(c, htf, ef)   # 추세강화 게이트 (기본 전부 OFF)
        return {"allowed": allowed, "htf_ema": htf_ema, "range": rng,
                "level": level, "broke": broke, "atr": a, "stop": stop,
                "conf": conf,
                "ready": bool(broke and rng > 0 and conf["ok"]
                              and stop is not None),
                "entry_ref": cur.close}

    def evaluate(self, ctx: TradingContext) -> TradeSignal | None:
        g = self._gates(ctx)
        if not g or not g["ready"]:
            return None
        side = g["allowed"]
        return TradeSignal(
            side=side, signal_type="VBO", strength=70,
            stop_price=g["stop"], entry_hint=g["entry_ref"],
            detail=(f"VBO K{self.cfg.vbo_k:g}×range {g['range']:.4g} "
                    f"{'>' if side is Side.LONG else '<'} {g['level']:g}"))

    def diagnose(self, ctx: TradingContext) -> dict | None:
        g = self._gates(ctx)
        if not g:
            return None
        allowed = g["allowed"].value
        gates = [
            {"key": "trend", "label": f"HTF 추세(EMA{self.cfg.donchian_htf_ema})",
             "ok": True, "info": f"{allowed} · vs {g['htf_ema']:.2f}"},
            {"key": "vbo", "label": f"K{self.cfg.vbo_k:g} 변동성 돌파",
             "ok": bool(g["broke"]), "info": f"→ {g['level']:.2f}"},
            {"key": "atr", "label": "ATR 스탑 확보", "ok": bool(g["atr"]),
             "info": f"ATR {g['atr']:.4f}" if g["atr"] else "–"},
        ]
        gates += confirmation_rows(self.cfg, g["conf"])
        passed = sum(1 for x in gates if x["ok"])
        return {"allowed": allowed, "ready": g["ready"],
                "passed": passed, "total": len(gates), "gates": gates,
                "atr": round(g["atr"], 4) if g["atr"] else None,
                "box_hi": round(g["level"], 6), "box_lo": round(g["level"], 6),
                "stop_preview": round(g["stop"], 6) if g["stop"] else None}
