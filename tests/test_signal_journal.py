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
    test_persists_across_instances()
    print("\nALL signal journal tests passed")
