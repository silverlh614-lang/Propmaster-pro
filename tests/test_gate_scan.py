"""Offline tests for the candidate gate-scan CLI (scripts/gate_scan.py).
No network — only the pure formatting/selection helpers are exercised with
synthetic rows. Run:  python -m tests.test_gate_scan"""
from __future__ import annotations

from scripts.gate_scan import _fmt, _promotions


def test_fmt():
    assert _fmt(None) == "-"
    assert _fmt(None, "{:.2f}") == "-"
    assert _fmt(20) == "20"
    assert _fmt(1.23456, "{:.3f}") == "1.235"
    print("ok  _fmt (None -> dash, formats otherwise)")


def test_promotions_best_strategy_per_symbol():
    rows = [
        {"symbol": "ADA", "strategy": "prop_breakout", "gate_pass": True,
         "expectancy_r": 0.10},
        {"symbol": "ADA", "strategy": "vbo", "gate_pass": True,
         "expectancy_r": 0.25},                         # higher -> wins
        {"symbol": "DOGE", "strategy": "prop_breakout", "gate_pass": False,
         "expectancy_r": 0.30},                         # failed gate -> excluded
        {"symbol": "BNB", "strategy": "vbo", "gate_pass": True,
         "expectancy_r": None},                         # passing but None expR
    ]
    promo = _promotions(rows)
    assert set(promo) == {"ADA", "BNB"}, promo
    assert promo["ADA"]["strategy"] == "vbo"
    print("ok  _promotions (gate-only, best expectancy_r per symbol)")


def test_promotions_empty_when_none_pass():
    rows = [{"symbol": "X", "strategy": "vbo", "gate_pass": False,
             "expectancy_r": 0.5}]
    assert _promotions(rows) == {}
    print("ok  _promotions empty when nothing passes the gate")


def main():
    test_fmt()
    test_promotions_best_strategy_per_symbol()
    test_promotions_empty_when_none_pass()
    print("\nALL gate-scan tests passed")


if __name__ == "__main__":
    main()
