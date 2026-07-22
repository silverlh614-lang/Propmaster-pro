"""@responsibility 확인 게이트 — 거래량·장악형·횡보·ADX·브레이크리테스트 필터, 돌파 전략 공용 (기본 OFF)

Trend-reinforcement confirmation gates shared by the breakout strategies.
The source theory's defense against box-range (박스권) whipsaw, as four
independent checks — every knob is OFF by default and may only be enabled
through the Phase 2 backtest gate (no hand-tuning):

  volume_gate_mult — 거래량=세력: the breakout bar must carry volume
      (>= mult x SMA(volume, volume_ma_period)). A no-volume poke out of a
      box is the classic false breakout that bleeds in sideways markets.
  engulf_gate      — 변동성 군집/장악형: the breakout bar's BODY must exceed
      the previous bar's body — the range energy that is supposed to carry
      into the next bar has to actually exist.
  chop_gate_flips  — 횡보장도 추세다: if the HTF close flipped sides against
      its EMA >= N times over the last chop_window bars, the market is
      ranging — stand aside instead of trading every fake break.
  adx_gate_min     — 추세강도(ADX): Wilder's ADX must be >= N on the entry
      TF. Where chop_gate_flips reads oscillation, ADX reads directional
      push — a weak-ADX break has no trend behind it. Direction-agnostic
      (the HTF EMA already picks the side); this only vetoes weak regimes.
"""
from __future__ import annotations

from ..indicators import adx, ema_flip_count, sma
from ..models import Candle, Side


def retest_confirmed(cfg, ef: list[Candle], allowed: Side,
                     atr_val: float | None) -> tuple[bool, dict]:
    """브레이크-리테스트 확인 (stateless, 최근 봉 윈도우 패턴). retest_confirm off 면
    항상 (True, …) — 기저 동작 그대로. on 이면 세 조건 모두 충족 시 True:
      (1) 브레이크 — 최근 retest_window 봉 중 하나가 '사전 채널'(최근 봉 제외) 극단을
          종가로 돌파,
      (2) 리테스트 — 같은 구간에서 가격이 그 레벨 ±band(=retest_band_atr×ATR) 존까지
          되돌림,
      (3) 재장악 — 현재 봉 종가가 다시 레벨 너머.
    초기 찌름(찌르고 붕괴하는 가짜돌파)을 걸러 진입을 '검증된 되돌림 후 재장악'으로 미룬다."""
    if not cfg.retest_confirm:
        return True, {"level": None}
    r, lb = cfg.retest_window, cfg.donchian_lookback
    if len(ef) < lb + r + 2 or not atr_val or atr_val <= 0:
        return False, {"level": None}
    pre = ef[-(lb + r + 1):-(r + 1)]          # 브레이크 이전 채널 (최근 r봉 제외)
    recent = ef[-(r + 1):-1]                   # 최근 r봉 = 브레이크+리테스트 구간 (현재 제외)
    cur = ef[-1]
    band = cfg.retest_band_atr * atr_val
    if allowed is Side.LONG:
        level = max(x.high for x in pre)
        broke = any(x.close > level for x in recent)
        retested = min(x.low for x in recent) <= level + band
        held = cur.close > level
    else:
        level = min(x.low for x in pre)
        broke = any(x.close < level for x in recent)
        retested = max(x.high for x in recent) >= level - band
        held = cur.close < level
    ok = bool(broke and retested and held)
    return ok, {"level": level, "broke": broke,
                "retested": retested, "held": held}


def confirmation_gates(cfg, htf: list[Candle], ef: list[Candle]) -> dict:
    """Evaluate the optional confirmations on the CURRENT entry bar. Callers
    AND `ok` into their ready flag; checks whose knob is inactive stay True,
    so all-default configs reproduce pre-gate behaviour exactly."""
    cur, prev = ef[-1], ef[-2]
    out: dict = {"volume_ok": True, "engulf_ok": True, "chop_ok": True,
                 "adx_ok": True, "vol_ratio": None, "flips": None,
                 "adx_val": None}
    if cfg.volume_gate_mult > 0:
        base = sma([x.volume for x in ef[:-1]], cfg.volume_ma_period)
        if base and base > 0:
            out["vol_ratio"] = cur.volume / base
            out["volume_ok"] = cur.volume >= cfg.volume_gate_mult * base
        else:
            out["volume_ok"] = False        # no volume sample = no confirmation
    if cfg.engulf_gate:
        out["engulf_ok"] = cur.body > prev.body
    if cfg.chop_gate_flips > 0:
        flips = ema_flip_count([x.close for x in htf],
                               cfg.donchian_htf_ema, cfg.chop_window)
        out["flips"] = flips
        out["chop_ok"] = flips is not None and flips < cfg.chop_gate_flips
    if cfg.adx_gate_min > 0:
        val = adx(ef, cfg.adx_period)
        out["adx_val"] = val
        out["adx_ok"] = val is not None and val >= cfg.adx_gate_min
    out["ok"] = bool(out["volume_ok"] and out["engulf_ok"]
                     and out["chop_ok"] and out["adx_ok"])
    return out


def confirmation_rows(cfg, conf: dict) -> list[dict]:
    """Dashboard gate rows for the active confirmations (diagnose contract) —
    a knob that is off contributes no row, mirroring pump/squeeze."""
    rows: list[dict] = []
    if cfg.volume_gate_mult > 0:
        ratio = conf["vol_ratio"]
        rows.append({"key": "volume", "label": "거래량 동반(세력)",
                     "ok": bool(conf["volume_ok"]),
                     "info": (f"×{ratio:.2f} ≥ ×{cfg.volume_gate_mult:g}"
                              if ratio is not None else "표본 부족")})
    if cfg.engulf_gate:
        rows.append({"key": "engulf", "label": "장악형 몸통",
                     "ok": bool(conf["engulf_ok"]), "info": "몸통 > 직전봉"})
    if cfg.chop_gate_flips > 0:
        rows.append({"key": "chop", "label": "횡보장 관망(EMA 플립)",
                     "ok": bool(conf["chop_ok"]),
                     "info": (f"{conf['flips']}회 < {cfg.chop_gate_flips}회"
                              f"/{cfg.chop_window}봉"
                              if conf["flips"] is not None else "표본 부족")})
    if cfg.adx_gate_min > 0:
        val = conf["adx_val"]
        rows.append({"key": "adx", "label": "추세강도(ADX)",
                     "ok": bool(conf["adx_ok"]),
                     "info": (f"{val:.1f} ≥ {cfg.adx_gate_min:g}"
                              if val is not None else "표본 부족")})
    return rows
