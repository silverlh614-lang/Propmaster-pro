"""@responsibility 후보 게이트 스캔 CLI — CANDIDATE_SPECS 를 Phase2 게이트로 백테스트해 승격 후보 표 출력 (데이터 접근 필요)

Candidate gate-scan runner (research convenience).

Runs the SAME Phase 2 gate the roster uses — scan_universe() → _gate_pass:
expectancy_r > 0 AND profit_factor >= 1.2 AND trades >= 20 — over the
backtest-only candidate pool, prints a table sorted gate-passers first, and
proposes promotions (best strategy per passing symbol).

NEEDS NETWORK to the Binance vision archive (data.binance.vision) for
months>0, or fapi.binance.com / OKX for months=0. Run on Railway or an
allowed-region machine; a restricted sandbox where those hosts are blocked
will report every symbol as a fetch error (handled gracefully).

Usage:
  python scripts/gate_scan.py                     # 후보풀 전체, 12mo, prop_breakout+vbo
  python scripts/gate_scan.py --symbols ADA,DOGE  # 특정 심볼만
  python scripts/gate_scan.py --months 12 --strategies prop_breakout,vbo
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))   # repo root on path

from app.trading.backtest.engine import scan_universe   # noqa: E402
from app.trading.config import CANDIDATE_SPECS, CONFIG   # noqa: E402
from app.trading.strategies import STRATEGIES   # noqa: E402


def _fmt(v, spec="{}") -> str:
    return "-" if v is None else spec.format(v)


def _promotions(rows: list[dict]) -> dict:
    """Gate-passing rows collapsed to the best (highest expectancy_r) strategy
    per symbol — the promotion shortlist."""
    best: dict[str, dict] = {}
    for r in rows:
        if not r.get("gate_pass"):
            continue
        cur = best.get(r["symbol"])
        if cur is None or (r["expectancy_r"] or -999) > (cur["expectancy_r"] or -999):
            best[r["symbol"]] = r
    return best


def _print_table(rows: list[dict]) -> None:
    hdr = (f"{'SYMBOL':<7} {'STRAT':<13} {'TRADES':>6} {'WIN%':>6} "
           f"{'EXP_R':>7} {'PF':>6} {'RET%':>8} {'MDD$':>9}  GATE")
    print(hdr)
    print("-" * len(hdr))
    for r in rows:
        if r.get("error"):
            print(f"{r['symbol']:<7} {'(error)':<13} {r['error']}")
            continue
        winpct = None if r["win_rate"] is None else round(r["win_rate"] * 100, 1)
        print(f"{r['symbol']:<7} {r['strategy']:<13} "
              f"{_fmt(r['trades']):>6} {_fmt(winpct):>6} "
              f"{_fmt(r['expectancy_r'], '{:.3f}'):>7} "
              f"{_fmt(r['profit_factor'], '{:.2f}'):>6} "
              f"{_fmt(r['return_pct'], '{:.1f}'):>8} "
              f"{_fmt(r['max_drawdown_usd'], '{:.2f}'):>9}  "
              f"{'✅PASS' if r['gate_pass'] else '·'}")


def _progress(done: int, total: int, rows: list[dict]) -> None:
    print(f"  ...스캔 {done}/{total}", file=sys.stderr)


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="후보 풀 Phase2 게이트 스캔")
    ap.add_argument("--symbols", default="",
                    help="CSV 심볼 (기본: CANDIDATE_SPECS 전체)")
    ap.add_argument("--months", type=int, default=12,
                    help="백테스트 개월 0..60 (기본 12, 0=라이브 1000봉)")
    ap.add_argument("--strategies", default="prop_breakout,vbo")
    args = ap.parse_args(argv)

    symbols = ([s.strip().upper() for s in args.symbols.split(",") if s.strip()]
               or list(CANDIDATE_SPECS))
    strategies = [s.strip() for s in args.strategies.split(",") if s.strip()]
    for s in strategies:
        if s not in STRATEGIES:
            print(f"알 수 없는 전략 '{s}' — 있는 것: {list(STRATEGIES)}",
                  file=sys.stderr)
            return 2
    if not (0 <= args.months <= 60):
        print("months 는 0..60 범위", file=sys.stderr)
        return 2

    print(f"[gate-scan] {len(symbols)}종 × {len(strategies)}전략 × {args.months}mo "
          f"— 게이트: expR>0 · PF≥1.2 · trades≥20", file=sys.stderr)
    rows = scan_universe(symbols, strategies, CONFIG, months=args.months,
                         on_progress=_progress)
    print()
    _print_table(rows)

    scanned = {r["symbol"] for r in rows}
    errored = {r["symbol"] for r in rows if r.get("error")}
    if scanned and errored == scanned:
        print("\n⚠️  모든 심볼이 fetch 에러 — 거래소/vision 호스트 접근 불가 환경입니다.\n"
              "    네트워크가 열린 Railway/로컬(허용 리전)에서 실행하세요.",
              file=sys.stderr)
        return 1

    promo = _promotions(rows)
    print(f"\n게이트 통과 {sum(1 for r in rows if r.get('gate_pass'))}행 "
          f"/ 승격 후보 {len(promo)}종:")
    for sym, r in promo.items():
        print(f"  ✅ {sym} · {r['strategy']} — PF {r['profit_factor']:.2f}, "
              f"expR {r['expectancy_r']:.3f}, {r['trades']} trades")
    if promo:
        line = " · ".join(f"{s}:{r['strategy']}" for s, r in promo.items())
        print(f"\n승격 제안(심볼별 최고 전략): {line}")
        print("→ SYMBOL_SPECS 승격 + docs/phase2_results.md 에 근거 기록.")
    else:
        print("  (없음 — 이번 후보군에서 게이트 통과 심볼 없음)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
