"""Offline tests for auto-discovery — synthetic candles, no network.
Run:  python -m tests.test_discovery"""
from __future__ import annotations

import os
import tempfile

# isolate journal/state files before importing the package
_tmp = tempfile.mkdtemp(prefix="discovery-test-")
os.environ["DATA_DIR"] = _tmp

from app.trading.config import SYMBOL_SPECS, TradingConfig  # noqa: E402
from app.trading.discovery import (SymbolMetrics, compute_metrics,  # noqa: E402
                                   efficiency_ratio, rank,
                                   select_satellites)
from app.trading.models import Candle  # noqa: E402


def _c(ts, o, h, l, cl, v=100.0):
    return Candle(ts_ms=ts, open=o, high=h, low=l, close=cl, volume=v)


def _series(closes, vol=100.0):
    out = []
    for i, cl in enumerate(closes):
        o = closes[i - 1] if i else cl
        out.append(_c(i * 3_600_000, o, max(o, cl) * 1.01,
                      min(o, cl) * 0.99, cl, vol))
    return out


# --------------------------------------------------------- efficiency ratio

def test_efficiency_ratio():
    # clean trend: net move == path length -> ER 1.0
    trend = [100 + i for i in range(30)]
    assert abs(efficiency_ratio(trend, 20) - 1.0) < 1e-9
    # pure chop: big path, ~zero net move -> ER ~0
    chop = [100 + (5 if i % 2 == 0 else -5) for i in range(30)]
    assert efficiency_ratio(chop, 20) < 0.1
    # flat series: zero path -> 0.0, not a division error
    assert efficiency_ratio([100.0] * 30, 20) == 0.0
    # too few closes -> None
    assert efficiency_ratio([1.0, 2.0], 20) is None
    print("ok  efficiency ratio (trend 1.0 / chop ~0 / flat 0 / thin None)")


# ------------------------------------------------------------- metrics

def test_compute_metrics():
    trend = _series([100 + i for i in range(60)], vol=50.0)
    m = compute_metrics("X", trend, trend, er_window=20)
    assert m is not None
    # turnover = sum(close*volume) of the last 24 bars
    want = sum(c.close * c.volume for c in trend[-24:])
    assert abs(m.quote_vol_usdt - want) < 1e-6
    assert m.atr_pct > 0 and m.efficiency > 0.9
    assert m.score == round(m.efficiency * m.atr_pct, 6)

    # thin history -> None (never guess on a thin symbol)
    assert compute_metrics("X", trend[:10], trend, 20) is None
    assert compute_metrics("X", trend, trend[:5], 20) is None
    print("ok  compute_metrics (turnover/atr%/ER, thin -> None)")


def test_rank_liquidity_floor_and_order():
    liquid_trend = SymbolMetrics("A", 1e9, atr_pct=2.0, efficiency=0.8)
    liquid_chop = SymbolMetrics("B", 1e9, atr_pct=2.0, efficiency=0.1)
    illiquid = SymbolMetrics("C", 1e3, atr_pct=9.0, efficiency=0.9)
    ranked = rank([liquid_chop, illiquid, liquid_trend], min_quote_vol=1e6)
    # illiquid C is dropped whatever its score; best score first
    assert [m.key for m in ranked] == ["A", "B"]
    print("ok  rank (liquidity floor drops illiquid, best score first)")


# ------------------------------------------------------------- selection

def test_select_satellites():
    # plain fill: top_n from the ranking
    assert select_satellites(["A", "B", "C"], 2, held=[], active=[],
                             scanned=["A", "B", "C"]) == ["A", "B"]
    # hysteresis: a held position stays even when ranked out
    got = select_satellites(["A", "B", "C"], 2, held=["Z"],
                            active=["Z"], scanned=["A", "B", "C", "Z"])
    assert got[0] == "Z" and len(got) == 2 and "A" in got
    # held beyond top_n is never force-closed: all held kept
    got = select_satellites(["A"], 1, held=["Y", "Z"], active=["Y", "Z"],
                            scanned=["A", "Y", "Z"])
    assert got == ["Y", "Z"]
    # provider gap: an active satellite the scan missed is kept
    got = select_satellites(["A"], 2, held=[], active=["Q"], scanned=["A"])
    assert "Q" in got and "A" in got
    # scanned flat loser IS rotated out
    got = select_satellites(["A", "B"], 2, held=[], active=["Q"],
                            scanned=["A", "B", "Q"])
    assert got == ["A", "B"]
    print("ok  select_satellites (fill/hysteresis/provider-gap/rotation)")


# ------------------------------------------------------------- config

def test_config_defaults_and_universe():
    cfg = TradingConfig()
    assert cfg.auto_discovery is False, "discovery must default OFF"
    assert cfg.discovery_top_n >= 1 and cfg.discovery_interval_min >= 60
    # expanded universe: every new alt stays in the 2x class (roster
    # invariant also asserted in test_engine)
    for k in ("DOT", "ATOM", "NEAR", "APT", "ARB", "OP", "SUI", "UNI",
              "INJ", "TON"):
        assert k in SYMBOL_SPECS and SYMBOL_SPECS[k].leverage_cap == 2.0
    print("ok  config (discovery OFF by default, expanded universe 2x)")


# ------------------------------------------------- manager wiring (offline)

def test_manager_wiring():
    """Discovery is assembled with the manager, stays off by default, and
    _make_bot wires the shared journal/risk/ledger objects."""
    from app.trading.bot import TradingManager
    mgr = TradingManager(TradingConfig())
    d = mgr.discovery
    assert d.status()["enabled"] is False
    assert set(d.core) == set(mgr.bots)                # core = boot roster
    cand = [s.key for s in d.candidates()]
    assert not set(cand) & set(d.core), "core never appears as a candidate"
    bot = mgr._make_bot(SYMBOL_SPECS["SOL"])
    assert bot.journal is mgr.journal and bot.risk is mgr.risk
    assert bot.ledger is mgr.ledger
    # start() must be a no-op while the feature is off
    d.start()
    assert d._task is None
    print("ok  manager wiring (off by default, shared objects, core fixed)")


if __name__ == "__main__":
    test_efficiency_ratio()
    test_compute_metrics()
    test_rank_liquidity_floor_and_order()
    test_select_satellites()
    test_config_defaults_and_universe()
    test_manager_wiring()
    print("\nALL DISCOVERY TESTS PASSED")
