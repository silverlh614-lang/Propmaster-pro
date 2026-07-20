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
    test_persists_across_instances()
    print("\nALL signal journal tests passed")
