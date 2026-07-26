"""Offline tests for the signal journal — persistent record of every strategy
signal, decoupled from execution. No network. Run: python -m tests.test_signal_journal"""
from __future__ import annotations

import os
import tempfile

# isolate the signals CSV before importing the package
os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="signals-test-")

from app.trading.store import SignalJournal, SIGNAL_FIELDS, SIGNALS_CSV  # noqa: E402


def _fresh() -> SignalJournal:
    """A journal over an empty CSV — each test starts clean (shared DATA_DIR)."""
    SIGNALS_CSV.unlink(missing_ok=True)
    return SignalJournal()


def _row(sym="ETH", side="LONG", blocked="", entered=True):
    return {"symbol": sym, "strategy": "prop_breakout", "side": side,
            "signal_type": "PROP_BREAKOUT", "entry": 1823.21, "target": 1900.0,
            "stop": 1780.0, "detail": "Donchian55 break", "blocked": blocked,
            "entered": entered}


def test_append_and_tail():
    j = _fresh()
    j.append(_row("ETH", "LONG"))
    j.append(_row("SOL", "SHORT", blocked="prop budget guard", entered=False))
    rows = j.tail(10)
    assert len(rows) == 2
    # newest first
    assert rows[0]["symbol"] == "SOL" and rows[0]["side"] == "SHORT"
    assert rows[0]["blocked"] == "prop budget guard"
    # every field present + a ts stamped automatically
    assert set(SIGNAL_FIELDS) <= set(rows[0]) and rows[0]["ts"]
    # per-symbol filter
    eth = j.tail(10, symbol="eth")
    assert len(eth) == 1 and eth[0]["symbol"] == "ETH"
    print("ok  signal journal append + tail (newest-first, per-symbol filter)")


def test_stats_entered_vs_blocked():
    j = _fresh()
    j.append(_row("ETH", "LONG", entered=True))
    j.append(_row("XRP", "SHORT", blocked="max_concurrent_positions (1) reached",
                  entered=False))
    j.append(_row("ARB", "LONG", blocked="total_open_risk cap", entered=False))
    s = j.stats()
    assert s["records"] == 3 and s["entered"] == 1 and s["blocked"] == 2
    print("ok  signal journal stats (entered vs blocked = signal→exec gap)")


def test_forward_resolution_win_loss_expire():
    """전진 추적: 미결 시그널이 목표/손절 중 뭘 먼저 쳤는지 봉으로 판정하고, 미결이
    오래가면 EXPIRED. 확정분으로 라이브 win-rate·expectancy_r 를 낸다(체결 무관)."""
    import datetime as _dt
    j = _fresh()
    t0 = "2026-07-01T00:00:00+00:00"
    e0 = _dt.datetime.fromisoformat(t0).timestamp()
    bs = 3600

    def sig(sym, side, entry, target, stop):
        j.append({"symbol": sym, "strategy": "s", "side": side, "signal_type": "X",
                  "entry": entry, "target": target, "stop": stop, "detail": "",
                  "blocked": "", "entered": True, "ts": t0})

    sig("ETH", "LONG", 100, 120, 90)    # risk 10 → target = +2R
    sig("XRP", "SHORT", 100, 80, 110)   # stop 110 → −1R
    sig("ARB", "LONG", 100, 200, 50)    # neither yet → OPEN then EXPIRED
    now = e0 + 5 * bs
    j.resolve_open("ETH", high=125, low=101, now_ts=now, bar_seconds=bs, timeout_bars=168)
    j.resolve_open("XRP", high=111, low=99, now_ts=now, bar_seconds=bs, timeout_bars=168)
    j.resolve_open("ARB", high=130, low=95, now_ts=now, bar_seconds=bs, timeout_bars=168)
    by = {r["symbol"]: r for r in j.tail(10)}
    assert by["ETH"]["outcome"] == "WIN" and float(by["ETH"]["r_result"]) == 2.0
    assert by["XRP"]["outcome"] == "LOSS" and float(by["XRP"]["r_result"]) == -1.0
    assert by["ARB"]["outcome"] == "OPEN" and by["ETH"]["bars_held"] == "5"

    # ARB expires past the timeout window
    j.resolve_open("ARB", high=130, low=95, now_ts=e0 + 200 * bs,
                   bar_seconds=bs, timeout_bars=168)
    assert {r["symbol"]: r for r in j.tail(10)}["ARB"]["outcome"] == "EXPIRED"

    # forward stats: 2 resolved (1W/1L) → win_rate .5, expectancy (2 + -1)/2 = .5
    s = j.stats()
    assert s["resolved"] == 2 and s["wins"] == 1 and s["losses"] == 1
    assert s["win_rate"] == 0.5 and s["expectancy_r"] == 0.5 and s["expired"] == 1
    print("ok  forward resolution (win/loss/expire + live expectancy_r)")


def test_stop_first_when_bar_spans_both():
    """한 봉이 목표·손절을 동시에 스치면 손절 우선 (FSM 과 같은 보수적 규칙)."""
    j = _fresh()
    j.append({"symbol": "ETH", "strategy": "s", "side": "LONG", "signal_type": "X",
              "entry": 100, "target": 120, "stop": 90, "detail": "", "blocked": "",
              "entered": True, "ts": "2026-07-01T00:00:00+00:00"})
    # bar sweeps 88..125 — touches BOTH stop(90) and target(120) → LOSS
    j.resolve_open("ETH", high=125, low=88, now_ts=_epoch_plus(2), bar_seconds=3600,
                   timeout_bars=168)
    assert j.tail(1)[0]["outcome"] == "LOSS"
    print("ok  bar spanning stop+target resolves LOSS (conservative)")


def _epoch_plus(bars):
    import datetime as _dt
    return _dt.datetime.fromisoformat("2026-07-01T00:00:00+00:00").timestamp() + bars * 3600


def test_counterfactual_entered_vs_blocked():
    """반사실: 진입 시그널 vs 차단 시그널의 포워드 성과를 나눠 관문 성격을 진단한다.
    차단 표본이 문턱(10) 이상이고 -기대값이면 'gate_dodging_losers'(계좌 보호·정상)."""
    j = _fresh()

    def rec(sym, entered, blocked, outcome, r):
        # target/stop 을 비워 append 의 OPEN 덮어쓰기를 피하고 확정 결과를 직접 기록
        j.append({"symbol": sym, "strategy": "s", "side": "LONG", "signal_type": "X",
                  "blocked": blocked, "entered": entered,
                  "outcome": outcome, "r_result": r})

    rec("ETH", True, "", "WIN", 2.0)          # 진입: 2W(+2R)/1L(-1R) → expectancy +1.0
    rec("ETH", True, "", "WIN", 2.0)
    rec("ETH", True, "", "LOSS", -1.0)
    for _ in range(2):                          # 차단: 10건 확정, 대부분 패자 → -기대값
        rec("SOL", False, "prop budget guard", "WIN", 2.0)
    for _ in range(8):
        rec("SOL", False, "prop budget guard", "LOSS", -1.0)

    cf = j.counterfactual()
    assert cf["entered"] == {"resolved": 3, "wins": 2, "losses": 1,
                             "win_rate": 0.6667, "expectancy_r": 1.0}, cf["entered"]
    # 차단 기대값 = (2·2 + 8·-1)/10 = -0.4 → 관문이 패자를 회피
    assert cf["blocked"]["resolved"] == 10 and cf["blocked"]["expectancy_r"] == -0.4
    assert cf["verdict"] == "gate_dodging_losers", cf
    # 차단 표본이 문턱 미만이면 판단 보류
    assert j.counterfactual(symbol="ETH")["verdict"] == "insufficient"
    print("ok  signal counterfactual (entered vs blocked forward, verdict)")


def test_mfe_mae_excursion_tracking():
    """MFE/MAE: 미결 시그널이 봉마다 최대 유리(MFE)·불리(MAE) 이동을 R 로 누적하고,
    확정 후 결과별로 집계된다 — 진 거래가 손절 전 얼마나 유리했나, 이긴 거래가
    얼마나 역주행을 견뎠나."""
    import datetime as _dt
    j = _fresh()
    t0 = "2026-07-01T00:00:00+00:00"
    e0 = _dt.datetime.fromisoformat(t0).timestamp()
    bs = 3600

    def sig(sym, side, entry, target, stop):
        j.append({"symbol": sym, "strategy": "s", "side": side, "signal_type": "X",
                  "entry": entry, "target": target, "stop": stop, "detail": "",
                  "blocked": "", "entered": True, "ts": t0})

    # 단위 함수: LONG/SHORT 대칭 + 0 클램프
    fav, adv = SignalJournal._excursion({"side": "LONG", "entry": 100, "stop": 90}, 108, 98)
    assert round(fav, 3) == 0.8 and round(adv, 3) == 0.2
    fav, adv = SignalJournal._excursion({"side": "SHORT", "entry": 100, "stop": 110}, 103, 96)
    assert round(fav, 3) == 0.4 and round(adv, 3) == 0.3

    sig("ETH", "LONG", 100, 120, 90)   # risk 10 → 진 거래지만 +1.5R 까지 갔었다
    j.resolve_open("ETH", high=108, low=98, now_ts=e0 + bs, bar_seconds=bs, timeout_bars=168)
    j.resolve_open("ETH", high=115, low=96, now_ts=e0 + 2 * bs, bar_seconds=bs, timeout_bars=168)
    eth = {r["symbol"]: r for r in j.tail(5)}["ETH"]
    assert eth["outcome"] == "OPEN"          # 아직 미결이어도 MFE/MAE 는 누적
    assert float(eth["mfe_r"]) == 1.5 and float(eth["mae_r"]) == 0.4, eth
    # 손절 봉(low 89 ≤ 90) → LOSS, 최종 MAE 1.1 로 갱신
    j.resolve_open("ETH", high=105, low=89, now_ts=e0 + 3 * bs, bar_seconds=bs, timeout_bars=168)
    eth = {r["symbol"]: r for r in j.tail(5)}["ETH"]
    assert eth["outcome"] == "LOSS" and float(eth["r_result"]) == -1.0
    assert float(eth["mfe_r"]) == 1.5 and float(eth["mae_r"]) == 1.1, eth

    sig("XRP", "LONG", 100, 120, 90)   # 한 봉에 목표 도달(WIN), 그 봉 excursion 도 기록
    j.resolve_open("XRP", high=122, low=93, now_ts=e0 + bs, bar_seconds=bs, timeout_bars=168)
    xrp = {r["symbol"]: r for r in j.tail(5)}["XRP"]
    assert xrp["outcome"] == "WIN" and float(xrp["mfe_r"]) == 2.2 and float(xrp["mae_r"]) == 0.7

    exc = j.excursion()
    assert exc["samples"] == 2
    assert exc["loss_avg_mfe_r"] == 1.5      # 진 거래가 손절 전 평균 +1.5R
    assert exc["win_avg_mae_r"] == 0.7       # 이긴 거래가 평균 -0.7R 역주행 견딤
    print("ok  MFE/MAE excursion tracking + outcome-split aggregate")


def test_schema_migration_old_csv():
    """구 스키마(MFE/MAE 컬럼 이전) CSV 에 append 해도 헤더가 마이그레이션되고 기존
    행이 보존된다 — 열 어긋남 없이 라이브 signals.csv 를 안전하게 승계한다."""
    import csv as _csv
    SIGNALS_CSV.unlink(missing_ok=True)
    old = [f for f in SIGNAL_FIELDS if f not in ("mfe_r", "mae_r")]
    with SIGNALS_CSV.open("w", newline="", encoding="utf-8") as f:
        w = _csv.DictWriter(f, fieldnames=old)
        w.writeheader()
        w.writerow({**{k: "" for k in old}, "symbol": "OLD", "side": "LONG"})
    j = SignalJournal()
    j.append({"symbol": "NEW", "strategy": "s", "side": "LONG", "signal_type": "X",
              "entry": 100, "target": 120, "stop": 90, "blocked": "", "entered": True})
    rows = {r["symbol"]: r for r in j.tail(10)}
    assert set(rows) == {"OLD", "NEW"}                       # 기존 행 보존
    assert "mfe_r" in rows["OLD"] and "mfe_r" in rows["NEW"]  # 헤더 마이그레이션
    print("ok  signal CSV schema migration (old rows kept, header upgraded)")


def test_forward_breakdown_and_drift():
    """종목/전략별 포워드 분해 + 드리프트: 라이브 expR<0·표본충분이면 'decaying',
    ≥0이면 'holding', 표본 미달이면 'insufficient'."""
    from app.trading.signal_analysis import forward_breakdown
    j = _fresh()

    def rec(sym, strat, outcome, r):
        j.append({"symbol": sym, "strategy": strat, "side": "LONG", "signal_type": "X",
                  "blocked": "", "entered": True, "outcome": outcome, "r_result": r})
    # ETH(prop_breakout): 12건, 대부분 승 → +기대값 → holding
    for _ in range(9): rec("ETH", "prop_breakout", "WIN", 2.0)
    for _ in range(3): rec("ETH", "prop_breakout", "LOSS", -1.0)
    # SOL(vbo): 12건, 대부분 패 → -기대값 → decaying (재검증 대상)
    for _ in range(3): rec("SOL", "vbo", "WIN", 2.0)
    for _ in range(9): rec("SOL", "vbo", "LOSS", -1.0)
    # ARB: 4건뿐 → insufficient
    for _ in range(4): rec("ARB", "prop_breakout", "LOSS", -1.0)

    bs = forward_breakdown(j._rows(), "symbol")
    assert list(bs)[0] in ("ETH", "SOL")                # 최다 건수 앞
    assert bs["ETH"]["verdict"] == "holding" and bs["ETH"]["expectancy_r"] > 0
    assert bs["SOL"]["verdict"] == "decaying" and bs["SOL"]["expectancy_r"] < 0
    assert bs["ARB"]["verdict"] == "insufficient"
    # 전략 차원: prop_breakout = ETH(9W3L)+ARB(4L)=16건, vbo = SOL 12건
    st = forward_breakdown(j._rows(), "strategy")
    assert st["prop_breakout"]["resolved"] == 16 and st["vbo"]["resolved"] == 12
    assert st["vbo"]["verdict"] == "decaying"
    print("ok  forward breakdown by symbol/strategy + drift verdict")


def test_baseline_anchored_drift():
    """baseline 앵커: 라이브 expR 이 백테스트 baseline 절반 미만이면 'eroding'(음수 전
    경보), 음수면 'decaying', baseline 근처 이상이면 'holding'. baseline 없으면 0 기준."""
    from app.trading.signal_analysis import (_drift_verdict, forward_breakdown,
                                             BACKTEST_BASELINE_R)
    # 단위: baseline 0.80 (floor 0.40)
    assert _drift_verdict(12, 1.0, 0.80) == "holding"       # baseline 이상
    assert _drift_verdict(12, 0.30, 0.80) == "eroding"      # 0 ≤ er < 0.40
    assert _drift_verdict(12, -0.1, 0.80) == "decaying"     # 음수 — 소멸
    assert _drift_verdict(5, 0.30, 0.80) == "insufficient"  # 표본 부족
    assert _drift_verdict(12, 0.30, None) == "holding"      # baseline 없음 → 0 기준
    # forward_breakdown 이 baseline_r 을 싣고 eroding 판정 (HYPE baseline 0.416 —
    # 2026-07-22 스캔 갱신 후 최대 baseline. ETH 는 0.138 로 재측정되어 이동)
    j = _fresh()

    def rec(o, r):
        j.append({"symbol": "HYPE", "strategy": "vbo", "side": "LONG",
                  "signal_type": "X", "blocked": "", "entered": True,
                  "outcome": o, "r_result": r})
    for _ in range(5): rec("WIN", 2.0)
    for _ in range(8): rec("LOSS", -1.0)            # er = (10-8)/13 ≈ 0.154 < 0.208
    b = forward_breakdown(j._rows(), "symbol")["HYPE"]
    assert b["baseline_r"] == BACKTEST_BASELINE_R["HYPE"] == 0.416
    assert 0 < b["expectancy_r"] < 0.416 * 0.5 and b["verdict"] == "eroding", b
    print("ok  baseline-anchored drift (eroding vs decaying vs holding)")


def test_persists_across_instances():
    """CSV persists — a fresh journal (same DATA_DIR) reads prior signals, so a
    redeploy never loses the record."""
    SignalJournal().append(_row("TAO", "SHORT"))
    # a brand-new instance sees the whole history on disk
    assert any(r["symbol"] == "TAO" for r in SignalJournal().tail(100))
    print("ok  signal journal persists across instances (survives redeploy)")


if __name__ == "__main__":
    test_append_and_tail()
    test_stats_entered_vs_blocked()
    test_forward_resolution_win_loss_expire()
    test_stop_first_when_bar_spans_both()
    test_counterfactual_entered_vs_blocked()
    test_mfe_mae_excursion_tracking()
    test_schema_migration_old_csv()
    test_forward_breakdown_and_drift()
    test_baseline_anchored_drift()
    test_persists_across_instances()
    print("\nALL signal journal tests passed")
