"""@responsibility 외부 시그널방 사후검증 — BehDark 데이터셋을 kline 으로 판정하는 연구 전용 API (매매 무관)

Research-only judge for an external Telegram signal room (BehDark VIP).
The operator captured the room's signal cards (2026-06-19 ~ 07-28 window,
screenshots parsed by hand); this module replays each signal against
Binance vision klines and reports what ACTUALLY happened — the same
target/stop arbitration our own forward tracker uses.

STRICTLY RESEARCH: nothing here touches strategies, the risk gate or the
prop rules. External signals are never trade triggers (invariant #3/#4) —
this exists to answer one question: does the subscription room have edge?

Judgment rules (documented so the verdict is reproducible):
  fill    — price must TOUCH the entry zone after post time; fill at the
            zone's near edge. TP1/SL before any touch = NO_FILL (the room
            itself cancels no-fill signals — LTC/SAND practice).
  outcome — after fill, walk 1h bars: SL touch vs TP ladder in order;
            same bar touching both = SL first (conservative, engine rule).
            After TP1 the stop moves to breakeven (room convention).
  R       — ladder accounting: equal fraction per target, remainder exits
            at BE (0R) after TP1 or at SL (−1R) before TP1.
"""
from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, HTTPException

_KST = dt.timezone(dt.timedelta(hours=9))


def _ts(date: str, time: str | None) -> float:
    """카드의 KST 표시 시각 → UTC epoch sec (시각 미상은 00:00 KST)."""
    t = dt.datetime.fromisoformat(f"{date}T{time or '00:00'}").replace(tzinfo=_KST)
    return t.astimezone(dt.timezone.utc).timestamp()


# 스샷 파싱 스냅샷 (2026-08-01) — (sym, side, date, time, [entry near,far],
# [targets...], stop). 카드 미확보(GWEI·LTC·BNB)는 판정 불가로 제외.
SIGNALS: list[dict] = [dict(
    symbol=s, side=sd, ts=_ts(d, tm), entry=e, targets=tg, stop=st)
    for s, sd, d, tm, e, tg, st in [
        ("ARBUSDT", "SHORT", "2026-06-21", "19:22", [0.08240, 0.08509], [0.07881, 0.07516], 0.08829),
        ("PNUTUSDT", "SHORT", "2026-06-21", "20:54", [0.04391, 0.04502], [0.04222, 0.03938], 0.04632),
        ("DYDXUSDT", "SHORT", "2026-06-24", "15:57", [0.1487, 0.1510], [0.1409, 0.1334], 0.1584),
        ("ATOMUSDT", "SHORT", "2026-06-24", "01:42", [1.846, 1.941], [1.741, 1.554], 2.045),
        ("NEARUSDT", "LONG", "2026-06-30", "00:02", [1.838, 1.771], [1.974, 2.150, 2.275], 1.660),
        ("ZEREBROUSDT", "SHORT", "2026-06-30", "01:10", [0.041139, 0.043539], [0.037266, 0.032959, 0.028604, 0.024211], 0.047351),
        ("JTOUSDT", "SHORT", "2026-07-01", "01:17", [0.7808, 0.8144], [0.7017, 0.6210], 0.8871),
        ("ETHFIUSDT", "LONG", "2026-07-05", "19:38", [0.3852, 0.3504], [0.4108, 0.4594, 0.5383], 0.3253),
        ("ARBUSDT", "LONG", "2026-07-14", "01:27", [0.08963, 0.08391], [0.09570, 0.10546, 0.11543], 0.07863),
        ("CETUSUSDT", "LONG", "2026-07-15", "00:26", [0.01866, 0.01799], [0.01983, 0.02147, 0.02258], 0.01694),
        ("MOCAUSDT", "LONG", "2026-07-20", "01:31", [0.00854, 0.00832], [0.00902, 0.00965, 0.01038], 0.00781),
        ("RSRUSDT", "LONG", "2026-07-21", None, [0.001276, 0.001234], [0.001358, 0.001466, 0.001570], 0.001156),
        ("APTUSDT", "LONG", "2026-07-24", "00:48", [0.6143, 0.5878], [0.6485, 0.6975], 0.5521),
        ("NEIROUSDT", "LONG", "2026-07-26", "00:30", [0.00005547, 0.00005399], [0.00005887, 0.00006287, 0.00006524], 0.00005095),
        ("1000SHIBUSDT", "LONG", "2026-07-26", "01:01", [0.004587, 0.004437], [0.004963, 0.005454, 0.006312], 0.004080),
        ("VVVUSDT", "LONG", "2026-07-28", "03:23", [13.285, 12.062], [14.396, 16.860, 18.874], 11.134),
        ("WIFUSDT", "LONG", "2026-07-28", "20:34", [0.1542, 0.1463], [0.1625, 0.1773, 0.1900], 0.1368),
    ]]


def judge_signal(sig: dict, candles) -> dict:
    """한 시그널을 봉 목록(오름차순, Candle 또는 (ts_ms,o,h,l,c))으로 판정."""
    long = sig["side"] == "LONG"
    near = sig["entry"][0]
    hit = (lambda bar_lo, bar_hi, px: bar_lo <= px <= bar_hi)
    fill_i = None
    out = {"symbol": sig["symbol"], "side": sig["side"], "status": "NO_FILL",
           "tps_hit": 0, "r_ladder": None, "days_to_fill": None}
    bars = [(c.ts_ms, c.open, c.high, c.low, c.close) if hasattr(c, "ts_ms")
            else tuple(c) for c in candles]
    bars = [b for b in bars if b[0] / 1000.0 >= sig["ts"]]
    if not bars:
        out["status"] = "NO_DATA"
        return out
    for i, (ts, o, h, lo, c) in enumerate(bars):
        if hit(lo, h, near):                       # 진입존 근접변 터치 = 체결
            fill_i = i
            break
        if hit(lo, h, sig["targets"][0]):          # 체결 전 TP1 먼저 = 노필 취소
            out["status"] = "NO_FILL_RAN"
            return out
        if hit(lo, h, sig["stop"]):                # 체결 전 스탑 먼저 = 무효
            out["status"] = "NO_FILL_STOPPED"
            return out
    if fill_i is None:
        return out                                  # 데이터 끝까지 미체결
    fill = near
    risk = abs(fill - sig["stop"])
    out["days_to_fill"] = round((bars[fill_i][0] / 1000.0 - sig["ts"]) / 86400, 1)
    n = len(sig["targets"])
    frac = 1.0 / n
    stop = sig["stop"]
    reached = 0
    r_total = 0.0
    for ts, o, h, lo, c in bars[fill_i:]:
        sl_hit = (lo <= stop) if long else (h >= stop)
        while reached < n:
            tp = sig["targets"][reached]
            tp_hit = (h >= tp) if long else (lo <= tp)
            if not tp_hit:
                break
            if sl_hit and reached == 0:            # 같은 봉 SL+TP1 → SL 우선(보수)
                break
            reached += 1
            r_total += frac * abs(tp - fill) / risk
            stop = fill                            # TP1 후 본전 스탑 (관례)
        if sl_hit:
            if reached == 0:
                out.update(status="SL", tps_hit=0, r_ladder=round(-1.0, 3))
            else:                                   # 본전 청산 — 남은 물량 0R
                out.update(status=f"TP{reached}_THEN_BE", tps_hit=reached,
                           r_ladder=round(r_total, 3))
            return out
        if reached == n:
            out.update(status="ALL_TP", tps_hit=n, r_ladder=round(r_total, 3))
            return out
    out.update(status=f"OPEN_TP{reached}" if reached else "OPEN",
               tps_hit=reached, r_ladder=round(r_total, 3) if reached else None)
    return out


router = APIRouter(prefix="/api/trading/research", tags=["research"])


@router.get("/behdark")
def behdark(months: int = 3, tf: str = "60"):
    """전 시그널 kline 사후판정 (연구 전용 — 매매 무관). 첫 호출은 심볼별
    아카이브 다운로드로 수십 초 걸릴 수 있음(디스크 캐시됨)."""
    from .backtest.history import fetch_history
    if not (1 <= months <= 6):
        raise HTTPException(422, "months must be 1..6")
    rows, errors = [], {}
    for sig in SIGNALS:
        try:                                # 월별 미게시 구간은 일별로 메움
            candles = fetch_history(sig["symbol"], tf, months, daily_fallback=True)
        except Exception as e:                      # noqa: BLE001 — 심볼별 격리
            errors[sig["symbol"]] = f"{type(e).__name__}: {e}"[:120]
            continue
        if not candles:
            errors[sig["symbol"]] = "no archive months published"
            continue
        rows.append(judge_signal(sig, candles))
    # 체결 여부는 days_to_fill 로 판정한다 — 미결(OPEN)도 체결분이므로 분모에 든다
    filled = [r for r in rows if r["days_to_fill"] is not None]
    closed = [r for r in filled if not r["status"].startswith("OPEN")]
    rs = [r["r_ladder"] if r["r_ladder"] is not None else -1.0 for r in closed]
    tp1 = sum(1 for r in filled if r["tps_hit"] >= 1)
    return {"signals": len(SIGNALS), "judged": len(rows), "errors": errors,
            "no_data": sum(1 for r in rows if r["status"] == "NO_DATA"),
            "no_fill": sum(1 for r in rows if r["status"].startswith("NO_FILL")),
            "filled": len(filled), "open": len(filled) - len(closed),
            "closed": len(closed),
            "tp1_rate": round(tp1 / len(filled), 3) if filled else None,
            "avg_r_ladder": round(sum(rs) / len(rs), 3) if rs else None,
            "total_r": round(sum(rs), 3) if rs else None, "rows": rows}
