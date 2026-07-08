"""@responsibility 트레이딩 행위 감시 — 저널 스캔으로 마틴게일·과대 사이징·복수 매매 패턴 탐지

Conduct monitor. Real prop desks terminate accounts for gambling-style
risk conduct; this scans the trade journal (chronologically) for the
patterns a rule engine can actually see in a single-account simulator:

  MARTINGALE  — risk escalated >= MART_MULT after a loss, twice in a row
                (loss -> bigger bet -> loss -> bigger bet).
  OVERSIZE    — one entry risks more than the plan's ENTIRE daily-loss
                budget (size * daily_loss_pct), so a single stop-out can
                breach the daily rule on its own.
  REVENGE     — REVENGE_STREAK straight re-entries, each opened within
                REVENGE_COOLDOWN_S of the loss that preceded it.

scan() is stateless and idempotent: it returns every violation found in
the window, keyed by the triggering row's timestamp — the desk dedups
against what it already recorded. Detection never blocks an order
(sizing/risk caps do that); enforcement policy lives in the desk.
"""
from __future__ import annotations

import datetime as dt

MART_MULT = 1.5              # risk growth vs the previous entry = escalation
MART_STREAK = 2              # escalations in a row before flagging
REVENGE_COOLDOWN_S = 300     # re-entry within 5 min of a loss
REVENGE_STREAK = 3           # quick re-entries in a row before flagging

MARTINGALE = "martingale"
OVERSIZE = "oversize"
REVENGE = "revenge_trading"


def _ts(row: dict) -> float:
    try:
        return dt.datetime.fromisoformat(row["ts"]).timestamp()
    except Exception:
        return 0.0


def _risk(row: dict) -> float | None:
    try:
        v = float(row.get("risk_usd") or "")
        return v if v > 0 else None
    except (TypeError, ValueError):
        return None


def scan(rows: list[dict], account_size: float,
         daily_loss_pct: float) -> list[dict]:
    """Scan journal rows (any order) and return violations:
    [{key, kind, detail, ts}]. key is stable so callers can dedup."""
    rows = sorted((r for r in rows if r.get("ts")), key=_ts)
    out: list[dict] = []
    daily_budget = account_size * daily_loss_pct / 100.0

    prev_open_risk: float | None = None
    after_loss = False           # the last settled result was a loss
    last_loss_ts: float | None = None
    mart_streak = 0
    revenge_streak = 0

    for r in rows:
        ev, res = r.get("event"), r.get("result")
        if ev == "OPEN":
            risk = _risk(r)
            # -- oversize: one stop-out would eat the whole daily budget
            if risk is not None and daily_budget > 0 and risk > daily_budget:
                out.append({
                    "key": f"{OVERSIZE}:{r['ts']}", "kind": OVERSIZE,
                    "ts": r["ts"],
                    "detail": (f"1회 리스크 ${risk:.2f} > 일일손실 예산 "
                               f"${daily_budget:.2f} — 한 번의 손절이 일일 룰을 "
                               "단독으로 위반할 수 있는 사이즈")})
            # -- martingale: escalate after a loss, repeatedly
            if after_loss and risk is not None and prev_open_risk:
                if risk >= prev_open_risk * MART_MULT:
                    mart_streak += 1
                    if mart_streak >= MART_STREAK:
                        out.append({
                            "key": f"{MARTINGALE}:{r['ts']}", "kind": MARTINGALE,
                            "ts": r["ts"],
                            "detail": (f"손실 후 리스크 증액 {mart_streak}연속 "
                                       f"(${prev_open_risk:.2f} -> ${risk:.2f}, "
                                       f">= x{MART_MULT}) — 마틴게일 패턴")})
                else:
                    mart_streak = 0
            # -- revenge: chain of instant re-entries after losses
            if (after_loss and last_loss_ts
                    and _ts(r) - last_loss_ts <= REVENGE_COOLDOWN_S):
                revenge_streak += 1
                if revenge_streak >= REVENGE_STREAK:
                    out.append({
                        "key": f"{REVENGE}:{r['ts']}", "kind": REVENGE,
                        "ts": r["ts"],
                        "detail": (f"손실 직후({REVENGE_COOLDOWN_S // 60}분 내) "
                                   f"재진입 {revenge_streak}연속 — 복수 매매 패턴")})
            else:
                revenge_streak = 0
            if risk is not None:
                prev_open_risk = risk
            after_loss = False
        elif res == "LOSS":
            after_loss = True
            last_loss_ts = _ts(r)
        elif res in ("WIN", "CLOSED"):
            after_loss = False
            mart_streak = 0
            revenge_streak = 0
    return out
