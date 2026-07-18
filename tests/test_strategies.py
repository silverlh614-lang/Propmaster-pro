"""Offline tests for the prop strategy layer — synthetic candles, no
network. Run:  python -m tests.test_strategies"""
from __future__ import annotations

# tests.test_engine isolates DATA_DIR before importing the app package.
from tests.test_engine import _c, _coherent_series               # noqa: F401

from app.trading.config import TradingConfig               # noqa: E402
from app.trading.strategies import STRATEGIES, make_strategy  # noqa: E402
from app.trading.strategies.base import TradingContext     # noqa: E402


def _ctx(htf, entry):
    return TradingContext(symbol="BTC", htf_candles=htf, entry_candles=entry,
                        equity_usd=200, now=0)


def test_prop_breakout_donchian():
    """prop_breakout: Donchian channel break + HTF EMA filter, no
    engulfing/volume gates."""
    from app.trading.models import Side
    cfg = TradingConfig()
    cfg.donchian_lookback = 20          # mechanics test — pin the geometry
    strat = make_strategy("prop_breakout", cfg)
    htf = [_c(i * 3600000, 100 + i, 101 + i, 99 + i, 100.5 + i)
           for i in range(30)]                        # rising -> LONG only
    flat = [_c(i * 900000, 105, 110, 100, 105) for i in range(25)]
    # inside the channel: no signal
    inside = flat + [_c(25 * 900000, 105, 109, 104, 108)]
    assert strat.evaluate(_ctx(htf, inside)) is None
    # close above the 20-bar high (110): LONG with an ATR stop below entry
    burst = flat + [_c(25 * 900000, 106, 112, 105, 111.5)]
    sig = strat.evaluate(_ctx(htf, burst))
    assert sig is not None and sig.side is Side.LONG
    assert sig.signal_type == "PROP_BREAKOUT" and sig.stop_price < 111.5
    d = strat.diagnose(_ctx(htf, burst))
    assert d["ready"] and d["box_hi"] == 110 and d["passed"] == d["total"]
    # falling HTF flips the allowed side -> the same burst is not a SHORT
    htf_dn = [_c(i * 3600000, 130 - i, 131 - i, 129 - i, 130.5 - i)
              for i in range(30)]
    assert strat.evaluate(_ctx(htf_dn, burst)) is None
    print("ok  prop_breakout donchian breakout + HTF filter")


def test_prop_breakout_optional_filters():
    """Pump filter and squeeze gate veto breakouts only when enabled."""
    from app.trading.models import Side
    htf = [_c(i * 3600000, 100 + i, 101 + i, 99 + i, 100.5 + i)
           for i in range(30)]
    flat = [_c(i * 900000, 105, 110, 100, 105) for i in range(25)]
    burst = flat + [_c(25 * 900000, 106, 132, 105, 131)]   # +31% above ch_lo

    cfg = TradingConfig(); cfg.donchian_lookback = 20
    base = make_strategy("prop_breakout", cfg)
    assert base.evaluate(_ctx(htf, burst)) is not None      # filters off: fires

    cfg_p = TradingConfig(); cfg_p.donchian_lookback = 20; cfg_p.pump_filter_pct = 15.0
    pumped = make_strategy("prop_breakout", cfg_p)
    assert pumped.evaluate(_ctx(htf, burst)) is None        # +31% > 15% veto
    calm = flat + [_c(25 * 900000, 106, 112, 105, 111.5)]   # +11.5% run-up
    sig = pumped.evaluate(_ctx(htf, calm))
    assert sig is not None and sig.side is Side.LONG
    d = pumped.diagnose(_ctx(htf, burst))
    assert any(x["key"] == "pump" and not x["ok"] for x in d["gates"])

    cfg_s = TradingConfig(); cfg_s.donchian_lookback = 20; cfg_s.squeeze_gate = True
    squeezed = make_strategy("prop_breakout", cfg_s)
    # wide-range window (range 10, sd 0 but ATR 10): 2σ(0) < 1.5·ATR — flat
    # closes give σ=0 so the squeeze passes; alternate closes widen σ
    noisy = [_c(i * 900000, 105, 110, 100, 102 + 6 * (i % 2)) for i in range(25)]
    noisy_burst = noisy + [_c(25 * 900000, 106, 112, 105, 111.5)]
    d = squeezed.diagnose(_ctx(htf, noisy_burst))
    assert any(x["key"] == "squeeze" for x in d["gates"])
    assert squeezed.evaluate(_ctx(htf, calm)) is not None   # σ=0 window passes
    print("ok  prop_breakout optional pump/squeeze filters")


def test_vbo_volatility_breakout():
    """vbo: Larry Williams K-rule — break of open + K*prior-range in the HTF
    direction, distinct from Donchian's extreme break."""
    from app.trading.models import Side
    cfg = TradingConfig(); cfg.vbo_range_bars = 10; cfg.vbo_k = 0.5
    strat = make_strategy("vbo", cfg)
    htf = [_c(i * 3600000, 100 + i, 101 + i, 99 + i, 100.5 + i)
           for i in range(30)]                           # rising -> LONG only
    # prior 10 bars span range ~10 (110-100); level = open + 0.5*10
    flat = [_c(i * 900000, 105, 110, 100, 105) for i in range(15)]
    quiet = flat + [_c(15 * 900000, 106, 108, 105, 107)]  # close 107 < 106+5
    assert strat.evaluate(_ctx(htf, quiet)) is None
    burst = flat + [_c(15 * 900000, 106, 118, 105, 117)]  # close 117 > 111
    sig = strat.evaluate(_ctx(htf, burst))
    assert sig is not None and sig.side is Side.LONG and sig.signal_type == "VBO"
    assert sig.stop_price < 117
    # falling HTF forbids the long
    htf_dn = [_c(i * 3600000, 130 - i, 131 - i, 129 - i, 130.5 - i)
              for i in range(30)]
    assert strat.evaluate(_ctx(htf_dn, burst)) is None
    print("ok  vbo volatility breakout (K-rule + HTF filter)")


def test_mean_revert_band_fade():
    """mean_revert: fade a stretch from the SMA — long below -Nσ, short above
    +Nσ, and stay flat inside the band. Counter-thesis to the breakout pair."""
    from app.trading.models import Side
    cfg = TradingConfig(); cfg.mr_mean_bars = 20; cfg.mr_entry_sd = 2.0
    # flat HTF so the trend guard (off by default) never blocks the fade
    htf = [_c(i * 3600000, 100, 101, 99, 100) for i in range(30)]
    # a flat mean (>= mr_mean_bars + atr_period padding) then a stretched close
    base = [_c(i * 900000, 100, 101, 99, 100) for i in range(25)]
    down = base + [_c(25 * 900000, 100, 100, 90, 91)]     # deep below band
    strat = make_strategy("mean_revert", cfg)
    sig = strat.evaluate(_ctx(htf, down))
    assert sig is not None and sig.side is Side.LONG
    assert sig.signal_type == "MEAN_REVERT" and sig.stop_price < 91
    # symmetric: a spike far ABOVE the mean -> short fade
    up = base + [_c(25 * 900000, 100, 110, 100, 109)]
    sig2 = strat.evaluate(_ctx(htf, up))
    assert sig2 is not None and sig2.side is Side.SHORT and sig2.stop_price > 109
    # inside the band: no signal
    calm = base + [_c(25 * 900000, 100, 101, 99, 100)]
    assert strat.evaluate(_ctx(htf, calm)) is None
    # trend guard on + strong HTF stretch -> stand aside (don't fade a trend)
    cfg.mr_trend_guard = 0.02
    htf_up = [_c(i * 3600000, 100 + i, 101 + i, 99 + i, 100 + i)
              for i in range(30)]                          # close 129 >> EMA
    assert make_strategy("mean_revert", cfg).evaluate(_ctx(htf_up, down)) is None
    print("ok  mean_revert band fade (long/short/inside + trend guard)")


def test_registry_and_replay_smoke():
    """The registry is prop-only, rejects unknown names, and every listed
    strategy replays a synthetic series without crashing."""
    from app.trading.backtest.engine import replay
    assert list(STRATEGIES) == ["prop_breakout", "vbo", "mean_revert"]
    try:
        make_strategy("trend_breakout", TradingConfig())
        assert False, "legacy strategy should be gone"
    except ValueError:
        pass
    htf, entry = _coherent_series(220)
    cfg = TradingConfig()
    cfg.entry_interval, cfg.htf_interval = "15", "60"   # series geometry
    cfg.donchian_lookback = 20
    for name in STRATEGIES:
        r = replay("BTC", name, cfg, entry_candles=entry, htf_candles=htf)
        assert r["snapshots"] > 0
    print("ok  registry is prop-only + replay smoke")


def test_per_symbol_strategy_map():
    """TRADING_SYMBOL_STRATEGY assigns each SymbolBot its own strategy at
    start; absent symbols fall back to the requested default."""
    import asyncio
    import os as _os
    from app.trading.config import TradingConfig
    from app.trading.bot import TradingManager

    _os.environ["TRADING_SYMBOLS"] = "BTC,SOL"
    _os.environ["TRADING_SYMBOL_STRATEGY"] = "SOL:vbo"

    async def run():
        mgr = TradingManager(TradingConfig())
        await mgr.start(mode="paper", strategy="prop_breakout")
        try:
            assert mgr.bots["BTC"].strategy_name == "prop_breakout"
            assert mgr.bots["SOL"].strategy_name == "vbo"
            assert type(mgr.bots["SOL"].strategy).__name__ == "VolatilityBreakoutStrategy"
        finally:
            await mgr.stop()
            await mgr.stop_feeds()
    try:
        asyncio.run(run())
    finally:
        del _os.environ["TRADING_SYMBOLS"], _os.environ["TRADING_SYMBOL_STRATEGY"]
    print("ok  per-symbol strategy map (SOL:vbo, BTC default)")


def test_default_symbol_strategy_fallback():
    """env(TRADING_SYMBOL_STRATEGY) 미설정 시 게이트 검증 기본 매핑이 적용된다:
    SOL/XRP는 vbo(Donchian이면 손실), ETH는 prop_breakout, 미지 심볼은 default."""
    import os as _os
    from app.trading.config import strategy_for, DEFAULT_SYMBOL_STRATEGY

    saved = _os.environ.pop("TRADING_SYMBOL_STRATEGY", None)
    try:
        assert strategy_for("SOL", "prop_breakout") == "vbo"   # 폴백이 손실전략 방지
        assert strategy_for("XRP", "prop_breakout") == "vbo"
        assert strategy_for("ETH", "prop_breakout") == "prop_breakout"
        assert strategy_for("ZZZ", "prop_breakout") == "prop_breakout"  # 미지 → default
        assert DEFAULT_SYMBOL_STRATEGY["SOL"] == "vbo"
        # env가 설정되면 그게 우선 (기본 매핑 무시)
        _os.environ["TRADING_SYMBOL_STRATEGY"] = "SOL:prop_breakout"
        assert strategy_for("SOL", "vbo") == "prop_breakout"
    finally:
        _os.environ.pop("TRADING_SYMBOL_STRATEGY", None)
        if saved is not None:
            _os.environ["TRADING_SYMBOL_STRATEGY"] = saved
    print("ok  default symbol-strategy fallback (SOL/XRP->vbo, env overrides)")


if __name__ == "__main__":
    test_per_symbol_strategy_map()
    test_default_symbol_strategy_fallback()
    test_prop_breakout_donchian()
    test_prop_breakout_optional_filters()
    test_vbo_volatility_breakout()
    test_mean_revert_band_fade()
    test_registry_and_replay_smoke()
    print("\nall strategy tests passed ✅")
