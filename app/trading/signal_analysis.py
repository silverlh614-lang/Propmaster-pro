"""@responsibility 시그널 포워드 분석 — 종목/전략별 엣지 분해 + 라이브 드리프트 판정 (순수 함수, 저널 무의존)

Forward-signal analytics as pure functions over signal-journal rows.

Kept out of store.py so the journal module stays under the 500-line cap and so
these read-only aggregations carry no persistence concerns. `forward_breakdown`
groups resolved signals by symbol (or strategy) and reports the live win-rate /
R-expectancy per group, with a drift verdict: a group whose live expectancy has
gone negative on a sufficient sample is flagged 'decaying' — the backtest-
promoted edge is eroding live, a re-validation candidate BEFORE losses pile up.
The verdict is a sign test on a sample floor (no magnitude threshold, no trading
knob) — its only use is to queue the next backtest sweep (invariant #5).
"""
from __future__ import annotations

from .store import SIGNAL_RESULTS, _CF_MIN_RESOLVED


def _drift_verdict(resolved: int, er: float | None) -> str:
    """라이브 포워드 기대값 부호로 엣지 상태를 판정. 표본이 문턱 미만이면 보류."""
    if resolved < _CF_MIN_RESOLVED or er is None:
        return "insufficient"          # 표본 부족 — 판단 보류
    return "decaying" if er < 0 else "holding"


def forward_breakdown(rows: list[dict], dim: str = "symbol") -> dict:
    """확정 시그널을 종목(dim='symbol') 또는 전략(dim='strategy')으로 그룹핑해
    resolved·승/패·승률·expectancy_r·드리프트 verdict 를 낸다. 건수 내림차순."""
    key = "strategy" if dim == "strategy" else "symbol"
    groups: dict[str, list] = {}
    for r in rows:
        if r.get("outcome") in SIGNAL_RESULTS:
            groups.setdefault(r.get(key) or "?", []).append(r)
    out = {}
    for g, rs in sorted(groups.items(), key=lambda kv: -len(kv[1])):
        wins = sum(1 for r in rs if r["outcome"] == "WIN")
        ers = [float(r["r_result"]) for r in rs if r.get("r_result") not in ("", None)]
        er = round(sum(ers) / len(ers), 3) if ers else None
        out[g] = {
            "resolved": len(rs), "wins": wins, "losses": len(rs) - wins,
            "win_rate": round(wins / len(rs), 4) if rs else None,
            "expectancy_r": er, "verdict": _drift_verdict(len(rs), er),
        }
    return out
