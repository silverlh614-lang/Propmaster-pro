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


# ------------------------------------------------- symbol toggle (offline)

def test_symbol_toggle():
    """토글 UI 백엔드: 후보 종목 켜고 끄기 + 코어 보호 + 재시작 영속 +
    discovery 로테이션 보호(수동 핀은 후보/로테이션에서 제외)."""
    import asyncio
    from app.trading.bot import TradingManager

    cfg = TradingConfig()
    mgr = TradingManager(cfg)
    core = set(mgr.core)

    # 켜기: 위성 봇 생성 + 수동 핀 + protected 에 포함
    r = asyncio.run(mgr.toggle_symbol("ARB", True))
    assert r["ok"] and "ARB" in mgr.bots and "ARB" in mgr.protected_keys()
    # 켠 봇도 공유 객체를 쓴다
    assert mgr.bots["ARB"].risk is mgr.risk and mgr.bots["ARB"].ledger is mgr.ledger
    # discovery 는 수동 핀을 후보로 보지 않는다 (로테이션 보호)
    assert "ARB" not in [s.key for s in mgr.discovery.candidates()]

    # 코어는 끌 수 없다
    core_key = next(iter(core))
    r = asyncio.run(mgr.toggle_symbol(core_key, False))
    assert not r["ok"] and "코어" in r["error"]

    # 미지 심볼 거부
    assert not asyncio.run(mgr.toggle_symbol("FOO", True))["ok"]

    # 영속: 새 매니저가 수동 선택(ARB)을 복원 (같은 DATA_DIR)
    mgr2 = TradingManager(cfg)
    assert "ARB" in mgr2.bots and "ARB" in mgr2.protected_keys()

    # 끄기: 봇 제거 + 핀 해제 + 장부 정리
    r = asyncio.run(mgr2.toggle_symbol("ARB", False))
    assert r["ok"] and "ARB" not in mgr2.bots and "ARB" not in mgr2.protected_keys()
    # 다시 새 매니저: ARB 안 돌아옴
    assert "ARB" not in TradingManager(cfg).bots
    print("ok  symbol toggle (add/core-lock/persist/discovery-protect/remove)")


def test_toggle_refuses_open_position():
    """열린 포지션이 있는 종목은 끌 수 없다 (청산 우선)."""
    import asyncio
    from app.trading.bot import TradingManager

    mgr = TradingManager(TradingConfig())
    asyncio.run(mgr.toggle_symbol("SUI", True))

    class _P:                       # 최소 오픈 포지션 스텁
        class state: value = "OPEN"
    class _PM:
        pos = _P()
    mgr.bots["SUI"].pm = _PM()
    r = asyncio.run(mgr.toggle_symbol("SUI", False))
    assert not r["ok"] and "포지션" in r["error"] and "SUI" in mgr.bots
    print("ok  toggle refuses removing a symbol with an open position")


if __name__ == "__main__":
    test_efficiency_ratio()
    test_compute_metrics()
    test_rank_liquidity_floor_and_order()
    test_select_satellites()
    test_config_defaults_and_universe()
    test_manager_wiring()
    test_symbol_toggle()
    test_toggle_refuses_open_position()
    print("\nALL DISCOVERY TESTS PASSED")
