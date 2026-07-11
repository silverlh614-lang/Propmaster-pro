"""@responsibility 프롭 기본 전략 — Donchian 채널 돌파 + HTF EMA 추세 필터, 게이트 최소화

Prop-optimized breakout — deliberately minimal: every extra discretionary
gate lowers trade count and makes the evaluation drag while fees burn.
Only the two gates that survive prop backtests everywhere:

  1. HTF trend filter    : HTF close vs its EMA sets the ALLOWED direction
                           (donchian_htf_ema).
  2. Donchian breakout   : entry close beyond the N-bar channel extreme
                           (donchian_lookback bars, current bar excluded).

Optional vetoes, OFF by default (A/B via the backtest gate only):
  pump_filter_pct — reject a breakout that is already X% past the channel's
  far side (NFI pump protection: don't buy the blow-off).
  squeeze_gate    — require the PREVIOUS bar in a volatility squeeze
  (BB(20,2) inside Keltner(20,1.5*ATR)): breakouts out of contraction.
  volume/engulf/chop — trend-reinforcement confirmations against box-range
  whipsaw, shared via strategies/filters.py (also OFF by default).

Stops stay ATR-anchored; position size is NOT this module's job — prop
budget sizing lives in the position FSM.
"""
from __future__ import annotations

from ..indicators import atr, ema
from ..models import Side, TradeSignal
from .base import TradingContext, TradingStrategy
from .filters import confirmation_gates, confirmation_rows


class PropBreakoutStrategy(TradingStrategy):
    name = "prop_breakout"

    def _gates(self, ctx: TradingContext) -> dict | None:
        c = self.cfg
        htf, ef = ctx.htf_candles, ctx.entry_candles
        if len(htf) < c.donchian_htf_ema + 2:
            return None
        if len(ef) < max(c.donchian_lookback, c.atr_period) + 2:
            return None

        htf_ema = ema([x.close for x in htf], c.donchian_htf_ema)[-1]
        allowed = Side.LONG if htf[-1].close >= htf_ema else Side.SHORT

        window = ef[-(c.donchian_lookback + 1):-1]   # N bars, current excluded
        ch_hi = max(x.high for x in window)
        ch_lo = min(x.low for x in window)
        cur = ef[-1]
        broke = (cur.close > ch_hi if allowed is Side.LONG
                 else cur.close < ch_lo)

        pump_ok = True
        if c.pump_filter_pct > 0:
            ref = ch_lo if allowed is Side.LONG else ch_hi
            runup = ((cur.close / ref - 1.0) if allowed is Side.LONG
                     else (ref / cur.close - 1.0)) * 100.0
            pump_ok = runup <= c.pump_filter_pct

        squeeze_ok = True
        if c.squeeze_gate:
            # BB(2σ) inside Keltner(1.5·ATR) reduces to 2σ < 1.5·ATR
            closes = [x.close for x in window]
            mid = sum(closes) / len(closes)
            sd = (sum((x - mid) ** 2 for x in closes) / len(closes)) ** 0.5
            a_prev = atr(ef[:-1], c.atr_period)
            squeeze_ok = bool(a_prev and 2 * sd < 1.5 * a_prev)

        a = atr(ef, c.atr_period)
        stop = None
        if a and a > 0:
            stop = (cur.close - a * c.atr_stop_mult if allowed is Side.LONG
                    else cur.close + a * c.atr_stop_mult)

        conf = confirmation_gates(c, htf, ef)   # 추세강화 게이트 (기본 전부 OFF)

        return {
            "allowed": allowed, "htf_ema": htf_ema,
            "channel_hi": ch_hi, "channel_lo": ch_lo,
            "broke": broke, "atr": a, "stop": stop,
            "pump_ok": pump_ok, "squeeze_ok": squeeze_ok, "conf": conf,
            "ready": bool(broke and pump_ok and squeeze_ok and conf["ok"]
                          and stop is not None),
            "entry_ref": cur.close,
        }

    def evaluate(self, ctx: TradingContext) -> TradeSignal | None:
        g = self._gates(ctx)
        if not g or not g["ready"]:
            return None
        side = g["allowed"]
        level = g["channel_hi"] if side is Side.LONG else g["channel_lo"]
        return TradeSignal(
            side=side, signal_type="PROP_BREAKOUT", strength=70,
            stop_price=g["stop"], entry_hint=g["entry_ref"],
            detail=(f"Donchian{self.cfg.donchian_lookback} "
                    f"{'high' if side is Side.LONG else 'low'} {level:g} "
                    f"broken @ {g['entry_ref']:g}"))

    def diagnose(self, ctx: TradingContext) -> dict | None:
        """Live gate snapshot in the dashboard's contract (channel drawn as
        the chart box via box_hi/box_lo)."""
        g = self._gates(ctx)
        if not g:
            return None
        allowed = g["allowed"].value
        gates = [
            {"key": "trend", "label": f"HTF 추세(EMA{self.cfg.donchian_htf_ema})",
             "ok": True, "info": f"{allowed} · vs {g['htf_ema']:.2f}"},
            {"key": "donchian", "label": f"Donchian{self.cfg.donchian_lookback} 돌파",
             "ok": bool(g["broke"]),
             "info": f"[{g['channel_lo']:.2f}, {g['channel_hi']:.2f}]"},
            {"key": "atr", "label": "ATR 스탑 확보", "ok": bool(g["atr"]),
             "info": f"ATR {g['atr']:.4f}" if g["atr"] else "–"},
        ]
        if self.cfg.pump_filter_pct > 0:
            gates.append({"key": "pump", "label": "펌프 필터",
                          "ok": bool(g["pump_ok"]),
                          "info": f"≤ +{self.cfg.pump_filter_pct:g}%"})
        if self.cfg.squeeze_gate:
            gates.append({"key": "squeeze", "label": "변동성 스퀴즈",
                          "ok": bool(g["squeeze_ok"]), "info": "BB⊂KC"})
        gates += confirmation_rows(self.cfg, g["conf"])
        passed = sum(1 for x in gates if x["ok"])
        return {"allowed": allowed, "ready": g["ready"],
                "passed": passed, "total": len(gates), "gates": gates,
                "atr": round(g["atr"], 4) if g["atr"] else None,
                "box_hi": g["channel_hi"], "box_lo": g["channel_lo"],
                "stop_preview": round(g["stop"], 6) if g["stop"] else None}
