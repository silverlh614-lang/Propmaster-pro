"""@responsibility 시그널 포워드 분석 — 종목/전략별 엣지 분해 + baseline 앵커 드리프트 판정 (순수 함수, 저널 무의존)

Forward-signal analytics as pure functions over signal-journal rows.

Kept out of store.py so the journal module stays under the 500-line cap and so
these read-only aggregations carry no persistence concerns. `forward_breakdown`
groups resolved signals by symbol (or strategy) and reports the live win-rate /
R-expectancy per group, with a drift verdict.

Drift verdict is BASELINE-ANCHORED: each symbol was promoted by a measured
backtest expectancy (BACKTEST_BASELINE_R, 12mo gate scan). Live forward
expectancy is compared to that proven baseline, not just to zero — so a symbol
whose edge is HALF its backtest value is flagged 'eroding' long before it goes
negative ('decaying'). The floor fraction is a monitoring heuristic (no trading
knob) — its only use is to queue the next backtest sweep (invariant #5).
"""
from __future__ import annotations

from .store import SIGNAL_RESULTS, _CF_MIN_RESOLVED

# 종목별 백테스트 baseline expR — 매핑된 전략의 12mo 게이트 스캔값 (docs/phase2_results.md,
# 2026-07-20 재스캔). "엣지가 유지되면 라이브가 도달해야 할 기대값"의 기준선.
BACKTEST_BASELINE_R: dict[str, float] = {
    "ETH": 0.80, "AVAX": 0.793, "HYPE": 0.416, "ARB": 0.325, "SUI": 0.31,
    "DOT": 0.261, "ZEC": 0.241, "POPCAT": 0.215, "RENDER": 0.211, "WLD": 0.21,
    "XRP": 0.203, "TAO": 0.198, "PNUT": 0.196, "TRUMP": 0.192, "S": 0.192,
    "SOL": 0.185, "OP": 0.159, "STX": 0.146, "LDO": 0.137, "JTO": 0.134,
    "ALGO": 0.133, "NEAR": 0.116,
}
# 라이브 expR 이 baseline 의 이 분율 미만이면 'eroding' — 감쇠 경보 문턱(모니터링
# 휴리스틱, 매매 임계값 아님). 0 미만은 'decaying'(엣지 소멸)로 더 강하게.
_DRIFT_FLOOR_FRAC = 0.5


def _drift_verdict(resolved: int, er: float | None,
                   baseline: float | None = None) -> str:
    """baseline 앵커 드리프트 판정. 표본 부족→insufficient, 음수→decaying,
    baseline 대비 절반 미만→eroding, 그 외→holding. baseline 없으면 0 기준 폴백."""
    if resolved < _CF_MIN_RESOLVED or er is None:
        return "insufficient"              # 표본 부족 — 판단 보류
    if er < 0:
        return "decaying"                  # 엣지 음전환 — 재검증 대상
    if baseline and baseline > 0 and er < baseline * _DRIFT_FLOOR_FRAC:
        return "eroding"                   # 양수이나 baseline 절반 미만 — 감쇠 경보
    return "holding"


def forward_breakdown(rows: list[dict], dim: str = "symbol") -> dict:
    """확정 시그널을 종목(dim='symbol') 또는 전략(dim='strategy')으로 그룹핑해
    resolved·승/패·승률·expectancy_r·baseline_r·드리프트 verdict 를 낸다. 건수 내림차순.
    baseline 앵커는 종목 차원에서만 (전략은 다종목 혼합이라 baseline None)."""
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
        base = BACKTEST_BASELINE_R.get(g) if key == "symbol" else None
        out[g] = {
            "resolved": len(rs), "wins": wins, "losses": len(rs) - wins,
            "win_rate": round(wins / len(rs), 4) if rs else None,
            "expectancy_r": er, "baseline_r": base,
            "verdict": _drift_verdict(len(rs), er, base),
        }
    return out
