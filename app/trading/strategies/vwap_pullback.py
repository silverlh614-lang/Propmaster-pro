"""@responsibility VWAP 되돌림 전략 — 추세방향으로 세션 VWAP 눌림 반등 매수/되돌림 반등 매도 (동적 레벨)

VWAP pullback — the dynamic-level cousin of htf_support. Instead of a static
structural level (channel extreme / swing low), the level is the intraday
session VWAP (volume-weighted average price, reset each UTC day), which RISES
with the trend. This is the key difference from a static break-retest: a
runaway uptrend never returns to an old breakout level (so retest missed it),
but it repeatedly pulls back to its rising VWAP — so a VWAP bounce rides the
trend instead of missing it.

  uptrend  (HTF close >= EMA)  -> LONG a bounce off VWAP from above
  downtrend (HTF close <  EMA)  -> SHORT a rejection at VWAP from below

A long fires when the entry bar dips INTO the VWAP zone (low within
vwap_band_atr * ATR of VWAP) and CLOSES back above it, AND the prior bar was
already above VWAP (a genuine pullback from above, not a fresh cross up). Stop
sits an ATR beyond VWAP (trend invalidation). Registered but NOT a default —
it earns live use on a symbol only by passing the Phase 2 backtest gate.
Signal only; the FSM owns sizing, target and trailing.
"""
from __future__ import annotations

from ..indicators import atr, ema
from ..models import Side, TradeSignal
from .base import TradingContext, TradingStrategy

_DAY_MS = 86_400_000


def session_vwap(ef, min_bars: int) -> tuple[float | None, int]:
    """현재 UTC 세션(일)의 거래량가중평균가(전형가 기준). 세션 봉이 min_bars 미만이거나
    거래량 합이 0이면 (None, n). 끝에서 역방향으로 당일 봉만 훑어 O(세션길이)."""
    cur_day = ef[-1].ts_ms // _DAY_MS
    num = den = 0.0
    n = 0
    for x in reversed(ef):
        if x.ts_ms // _DAY_MS != cur_day:
            break
        typ = (x.high + x.low + x.close) / 3.0
        num += typ * x.volume
        den += x.volume
        n += 1
    if n < min_bars or den <= 0:
        return None, n
    return num / den, n


class VwapPullbackStrategy(TradingStrategy):
    name = "vwap_pullback"

    def _gates(self, ctx: TradingContext) -> dict | None:
        c = self.cfg
        htf, ef = ctx.htf_candles, ctx.entry_candles
        if len(htf) < c.donchian_htf_ema + 2 or len(ef) < c.atr_period + 2:
            return None

        htf_ema = ema([x.close for x in htf], c.donchian_htf_ema)[-1]
        allowed = Side.LONG if htf[-1].close >= htf_ema else Side.SHORT

        vwap, n_sess = session_vwap(ef, c.vwap_min_bars)
        a = atr(ef, c.atr_period)
        cur, prev = ef[-1], ef[-2]
        zone = (a or 0.0) * c.vwap_band_atr
        touched = held = leg = None
        stop = None
        if vwap is not None and a and a > 0:
            if allowed is Side.LONG:                 # VWAP 눌림 반등 매수
                touched = cur.low <= vwap + zone     # VWAP 존까지 눌림
                held = cur.close > vwap              # 다시 위로 마감(반등)
                leg = prev.close > vwap              # 눌림 직전 이미 VWAP 위(진짜 되돌림)
                stop = vwap - a * c.atr_stop_mult
            else:                                    # VWAP 되돌림 반등 매도
                touched = cur.high >= vwap - zone
                held = cur.close < vwap
                leg = prev.close < vwap
                stop = vwap + a * c.atr_stop_mult
        return {"allowed": allowed, "htf_ema": htf_ema, "vwap": vwap,
                "session_bars": n_sess, "zone": zone, "atr": a, "stop": stop,
                "touched": bool(touched), "held": bool(held), "leg": bool(leg),
                "entry_ref": cur.close,
                "ready": bool(touched and held and leg and vwap is not None
                              and a and a > 0 and stop is not None)}

    def evaluate(self, ctx: TradingContext) -> TradeSignal | None:
        g = self._gates(ctx)
        if not g or not g["ready"]:
            return None
        side = g["allowed"]
        return TradeSignal(
            side=side, signal_type="VWAP_PULLBACK", strength=65,
            stop_price=g["stop"], entry_hint=g["entry_ref"],
            detail=(f"VWAP {g['vwap']:.4g} "
                    f"{'눌림반등매수' if side is Side.LONG else '되돌림반등매도'} "
                    f"@ {g['entry_ref']:.4g}"))

    def diagnose(self, ctx: TradingContext) -> dict | None:
        g = self._gates(ctx)
        if not g:
            return None
        allowed = g["allowed"].value
        vw = g["vwap"]
        gates = [
            {"key": "trend", "label": f"HTF 추세(EMA{self.cfg.donchian_htf_ema})",
             "ok": True, "info": f"{allowed} · vs {g['htf_ema']:.4g}"},
            {"key": "vwap", "label": "세션 VWAP 확보",
             "ok": vw is not None,
             "info": (f"{vw:.4g} · {g['session_bars']}봉" if vw is not None
                      else f"세션 {g['session_bars']}봉<{self.cfg.vwap_min_bars}")},
            {"key": "touch", "label": "VWAP 존 터치(눌림)", "ok": g["touched"],
             "info": f"±{g['zone']:.4g}"},
            {"key": "hold", "label": "VWAP 지켜 마감(반등)", "ok": g["held"],
             "info": "반등" if g["held"] else "이탈"},
            {"key": "leg", "label": "직전봉 추세측(진짜 되돌림)", "ok": g["leg"],
             "info": "위→눌림" if allowed == "LONG" else "아래→되돌림"},
            {"key": "atr", "label": "ATR 스탑 확보", "ok": bool(g["atr"]),
             "info": f"ATR {g['atr']:.4f}" if g["atr"] else "–"},
        ]
        passed = sum(1 for x in gates if x["ok"])
        return {"allowed": allowed, "ready": g["ready"],
                "passed": passed, "total": len(gates), "gates": gates,
                "atr": round(g["atr"], 4) if g["atr"] else None,
                "box_hi": round(vw, 6) if vw is not None else None,
                "box_lo": round(vw, 6) if vw is not None else None,
                "stop_preview": round(g["stop"], 6) if g["stop"] else None}
