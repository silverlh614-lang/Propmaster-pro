"""Offline tests for the leverage-margin trading package — synthetic
candles, no network. Run:  python -m tests.test_engine"""
from __future__ import annotations

import os
import tempfile

# isolate journal/state files before importing the package
_tmp = tempfile.mkdtemp(prefix="engine-test-")
os.environ["DATA_DIR"] = _tmp

from app.trading import indicators as ind          # noqa: E402
from app.trading.config import SYMBOL_SPECS, TradingConfig  # noqa: E402
from app.trading.execution.position import PositionManager  # noqa: E402
from app.trading.models import Candle, Side, TradeSignal  # noqa: E402
from app.trading.risk import RiskManager, size_position  # noqa: E402
from app.trading.store import BotState, Journal    # noqa: E402
from app.trading.strategies import make_strategy    # noqa: E402
from app.trading.strategies.base import TradingContext  # noqa: E402

BTC = SYMBOL_SPECS["BTC"]


def _c(ts, o, h, l, cl, v=100.0):
    return Candle(ts_ms=ts, open=o, high=h, low=l, close=cl, volume=v)


# ------------------------------------------------------------- indicators

def test_indicators():
    vals = [1, 2, 3, 4, 5, 6]
    e = ind.ema(vals, 3)
    assert len(e) == len(vals) and e[-1] > e[0]
    assert ind.sma([1, 2, 3, 4], 2) == 3.5
    assert ind.sma([1], 5) is None

    candles = [_c(i * 60000, 100, 102, 98, 101) for i in range(20)]
    a = ind.atr(candles, 14)
    assert a is not None and a > 0
    print("ok  indicators (ema/sma/atr)")


# ------------------------------------------------------------- sizing

def test_sizing():
    # $200 equity, 1% risk = $2; entry 100 stop 98 (dist 2) -> qty 1.0
    qty, risk, why = size_position(200, 1.0, 100, 98, BTC, 5.0)
    assert qty == 1.0 and abs(risk - 2.0) < 1e-9, (qty, risk, why)

    # leverage cap: huge risk clamps notional to equity*max
    qty, risk, why = size_position(100, 50.0, 100, 99, BTC, 5.0)
    assert abs(qty * 100 - 500) < 1e-6, (qty, "notional should clamp to 500")

    # below-min qty -> refuse
    qty, risk, why = size_position(100, 0.01, 50000, 49000, BTC, 5.0)
    assert qty == 0.0 and "min" in why
    print("ok  sizing (risk %, leverage cap, min qty)")


def test_symbol_leverage_caps():
    """Prop rule: majors 5x, alts 2x — the symbol class cap binds when it is
    tighter than the global hard cap (SOL clamps at 2x notional)."""
    sol = SYMBOL_SPECS["SOL"]
    assert BTC.effective_leverage_max(5.0) == 5.0
    assert sol.effective_leverage_max(5.0) == 2.0
    assert sol.effective_leverage_max(1.5) == 1.5   # global cap can be tighter
    # same trade, same equity: SOL notional clamps to equity*2, BTC to *5
    q_sol, _, _ = size_position(100, 50.0, 100, 99, sol,
                                sol.effective_leverage_max(5.0))
    assert abs(q_sol * 100 - 200) < 1e-6, (q_sol, "SOL notional should clamp to 200")
    q_btc, _, _ = size_position(100, 50.0, 100, 99, BTC,
                                BTC.effective_leverage_max(5.0))
    assert abs(q_btc * 100 - 500) < 1e-6, (q_btc, "BTC notional should clamp to 500")
    # roster invariant: ONLY the majors carry 5x, every alt is 2x
    for k, spec in SYMBOL_SPECS.items():
        want = 5.0 if k in ("BTC", "ETH") else 2.0
        assert spec.leverage_cap == want, (k, spec.leverage_cap)
        assert spec.symbol.endswith("USDT") and spec.qty_step > 0
    print("ok  per-symbol leverage caps (BTC/ETH 5x, alts 2x)")


# ------------------------------------------------------------- risk gate

def test_risk_gate():
    cfg = TradingConfig()
    cfg.max_concurrent_positions = 1
    cfg.max_total_open_risk_pct = 2.0        # $4 on $200
    risk = RiskManager(cfg, Journal(), BotState())
    ok, _ = risk.allow_entry(0, 0.0, 2.0)
    assert ok
    # second concurrent position blocked
    ok, why = risk.allow_entry(1, 2.0, 2.0)
    assert not ok and "concurrent" in why
    # add that would exceed total open-risk cap blocked
    ok, why = risk.allow_entry(1, 3.0, 2.0, is_add=True)
    assert not ok and "open_risk" in why
    # kill switch
    for _ in range(cfg.max_consecutive_errors):
        risk.record_error("boom")
    assert risk.kill_switch
    assert not risk.allow_entry(0, 0.0, 1.0)[0]
    risk.reset_kill()
    assert risk.allow_entry(0, 0.0, 1.0)[0]
    print("ok  risk gate (concurrent, open-risk cap, kill switch)")


def test_risk_caps_follow_compounded_equity():
    """The open-risk cap must be measured against CURRENT equity, not the
    starting stake."""
    cfg = TradingConfig()
    cfg.max_total_open_risk_pct = 2.0        # 2% of equity
    risk = RiskManager(cfg, Journal(), BotState())
    # $3 risk on $200 equity (cap $4) → allowed
    assert risk.allow_entry(0, 0.0, 3.0, equity_usd=200.0)[0]
    # same $3 risk after equity halved to $100 (cap $2) → blocked
    ok, why = risk.allow_entry(0, 0.0, 3.0, equity_usd=100.0)
    assert not ok and "open_risk" in why, why
    # after equity compounds to $400 (cap $8), $6 risk → allowed
    assert risk.allow_entry(0, 0.0, 6.0, equity_usd=400.0)[0]
    # no equity passed → falls back to config stake (back-compat)
    assert risk.allow_entry(0, 0.0, 3.0)[0]
    print("ok  risk caps follow compounded equity (총자산대비)")


# ------------------------------------------------------------- position FSM

def _pm(cfg=None):
    cfg = cfg or TradingConfig()
    risk = RiskManager(cfg, Journal(), BotState())
    return PositionManager(BTC, cfg, risk, Journal(), "paper", "trend_breakout")


def test_fsm_stop_loss():
    pm = _pm()
    sig = TradeSignal(Side.LONG, "T", 80, stop_price=98, entry_hint=100)
    assert pm.try_open(sig, 100, atr_val=2.0, ts=0)
    # candle dumps through the stop -> LOSS close at stop
    pm.manage(_c(60000, 100, 100.5, 97, 97.5), 2.0)
    assert pm.pos.state.value == "CLOSED"
    assert pm.pos.realized_pnl_usd < 0
    print("ok  fsm stop-loss")


def test_fsm_partial_then_trail():
    from app.trading import store
    store.TRADES_CSV.unlink(missing_ok=True)   # isolate the aggregate assertion
    cfg = TradingConfig()
    cfg.rr_target = 2.0
    cfg.partial_tp_frac = 0.5
    pm = _pm(cfg)
    sig = TradeSignal(Side.LONG, "T", 80, stop_price=98, entry_hint=100)
    assert pm.try_open(sig, 100, atr_val=2.0, ts=0)  # 1R = $2 dist, target 104
    # bar tags 2R target -> partial 50% + breakeven stop
    pm.manage(_c(60000, 100, 104.5, 100, 103), 2.0)
    assert pm.pos.partial_done and pm.pos.open_qty < 1.0
    assert pm.pos.trail_price is not None
    banked = pm.pos.realized_pnl_usd
    assert banked > 0
    # next bar pulls back to breakeven trail -> full close, still net positive
    # (total < banked partial only by the small exit fee on the breakeven leg)
    pm.manage(_c(120000, 103, 103.2, 99, 99.5), 2.0)
    assert pm.pos.state.value == "CLOSED"
    assert pm.pos.realized_pnl_usd > 0
    # journal: exactly one settled CLOSE row (no double counting the partial)
    agg = pm.journal.aggregate()
    assert agg["trades"] == 1 and agg["settled"] == 1, agg
    # PARTIAL row records its own realized cash + leg R (result stays blank)
    prow = next(r for r in pm.journal.tail(10) if r["event"] == "PARTIAL")
    assert prow["result"] == "" and float(prow["pnl_usd"]) > 0, prow
    assert float(prow["r_multiple"]) > 0, prow
    print("ok  fsm partial + trailing + single settled row + partial pnl logged")


def test_fsm_pyramiding():
    cfg = TradingConfig()
    cfg.pyramid_enabled = True
    cfg.pyramid_max_adds = 2
    cfg.pyramid_min_r = 1.0
    cfg.max_total_open_risk_pct = 10.0
    pm = _pm(cfg)
    sig = TradeSignal(Side.LONG, "T", 80, stop_price=98, entry_hint=100)
    assert pm.try_open(sig, 100, 2.0, 0)
    # price 1R ahead (102) -> add allowed
    add = TradeSignal(Side.LONG, "T", 80, stop_price=100, entry_hint=102)
    assert pm.try_add(add, 102, 2.0, 1)
    assert pm.pos.adds == 1 and len(pm.pos.units) == 2
    print("ok  fsm pyramiding add")


# ------------------------------------------------------------- strategy



def _downtrend_breakdown_lowvol():
    """HTF: 20-bar box [100,110] then a breakdown bar to 80.
    Entry: downtrend ending with a bearish engulfing but NO volume spike
    (vol == MA, below MA*1.2) — the H8 scenario from the source p31."""
    htf = []
    for i in range(24):
        base = 100 + (i % 3)
        htf.append(_c(i * 3600000, base, 110, 100, 105))
    htf.append(_c(24 * 3600000, 102, 103, 78, 80, 500))      # breakdown
    entry = []
    px = 130.0
    for i in range(28):
        entry.append(_c(i * 900000, px, px + 1, px - 1, px - 0.5, 100))
        px -= 0.5
    prev = _c(28 * 900000, 118.0, 119.0, 117.2, 118.6, 100)  # bullish
    cur = _c(29 * 900000, 118.8, 119.0, 114.0, 115.0, 100)   # bearish engulf, vol=MA
    entry += [prev, cur]
    return htf, entry



def _coherent_series(n=200):
    """One 15m price path (consolidation then uptrend) aggregated 4:1 into an
    aligned 1h series — so htf/entry timestamps line up like real klines."""
    import math
    entry = []
    for i in range(n):
        trend = 0.0 if i < 80 else (i - 80) * 0.6
        base = 100 + trend + 3 * math.sin(i / 3.0)
        o = base
        c = base + (0.9 if i % 2 == 0 else -0.9)
        h, l = max(o, c) + 0.7, min(o, c) - 0.7
        vol = 100 + (250 if i % 7 == 0 else 0)
        entry.append(_c(i * 900000, o, h, l, c, vol))
    htf = []
    for j in range(0, n - 3, 4):
        g = entry[j:j + 4]
        htf.append(_c(g[0].ts_ms, g[0].open, max(x.high for x in g),
                      min(x.low for x in g), g[-1].close, sum(x.volume for x in g)))
    return htf, entry


def test_backtest_replay():
    from app.trading.backtest.engine import replay
    from app.trading.backtest.metrics import compute
    cfg = TradingConfig()
    cfg.entry_interval, cfg.htf_interval = "15", "60"   # series geometry
    cfg.donchian_lookback = 20
    htf, entry = _coherent_series(220)
    r = replay("BTC", "prop_breakout", cfg, entry_candles=entry, htf_candles=htf)
    m = compute(r["closes"], cfg.equity_usd, r.get("final_equity", cfg.equity_usd))
    assert isinstance(m["trades"], int)
    assert r["snapshots"] > 0, "replay produced no evaluatable bars"
    print(f"ok  backtest replay (snapshots={r['snapshots']}, trades={m['trades']})")


def test_candles_export():
    """bot.candles() must build OHLCV + EMA when the collector has candles —
    regression for a missing `ema` import that 500'd the /candles endpoint
    only once real bars arrived (empty short-circuited past the bug)."""
    from app.trading.bot import TradingManager
    from app.trading.config import TradingConfig
    mgr = TradingManager(TradingConfig())
    bot = mgr.bots["BTC"]
    col = bot.collector
    px = 63000.0
    for i in range(30):
        ts = 1_700_000_000_000 + i * 900_000
        col._bars[col.entry_interval][ts] = Candle(ts, px, px + 50, px - 50, px + 10, 100 + i)
        px += 5
    out = bot.candles("entry", 120)
    assert len(out["candles"]) == 30 and len(out["ema"]) == 30, out
    assert out["candles"][0][0] < out["candles"][-1][0]     # ascending by time
    print("ok  candles export (ema import regression)")



def test_state_persistence():
    """A live open position must round-trip through PositionStore so a trade
    in progress survives a restart/redeploy. (Equity round-trips through the
    unified AccountLedger/AccountStore — tests/test_account_ledger.py.)"""
    from app.trading.config import TradingConfig, SYMBOL_SPECS
    from app.trading.execution.position import PositionManager
    from app.trading.models import Position, PositionState, Side, Unit
    from app.trading.risk import RiskManager
    from app.trading.store import BotState, Journal, PositionStore

    cfg = TradingConfig()
    spec = SYMBOL_SPECS["BTC"]
    j = Journal()
    risk = RiskManager(cfg, j, BotState())
    pm = PositionManager(spec, cfg, risk, j, "paper", "trend_breakout")
    # simulate a partially-managed open long
    pm.pos = Position(symbol="BTCUSDT", side=Side.LONG,
                      units=[Unit(Side.LONG, 63000.0, 0.01, 62500.0, 1.0,
                                  fee_usd=0.3)],
                      initial_risk_usd=5.0, target_price=64000.0,
                      trail_price=62800.0, realized_pnl_usd=2.1,
                      partial_done=True, state=PositionState.OPEN)
    pm.bars_in_trade = 9

    store = PositionStore()
    store.save("BTC", pm.to_state())
    assert "equity" not in store.load("BTC")       # equity는 계좌 원장 소관

    # a fresh manager (as after a redeploy) restores the exact state
    pm2 = PositionManager(spec, cfg, risk, j, "paper", "trend_breakout")
    pm2.load_state(store.load("BTC"))
    assert pm2.equity == cfg.equity_usd            # 심볼 기록은 equity 미보유
    assert pm2.bars_in_trade == 9
    p = pm2.pos
    assert p is not None and p.state == PositionState.OPEN and p.side == Side.LONG
    assert p.partial_done and abs(p.trail_price - 62800.0) < 1e-9
    assert abs(p.avg_entry - 63000.0) < 1e-6 and abs(p.open_qty - 0.01) < 1e-9
    assert abs(p.realized_pnl_usd - 2.1) < 1e-9

    # nothing saved for another symbol -> fresh start, no crash
    pm3 = PositionManager(spec, cfg, risk, j, "paper", "trend_breakout")
    pm3.load_state(store.load("ETH"))
    assert pm3.pos is None and pm3.equity == cfg.equity_usd
    print("ok  state persistence (equity + open position round-trip)")




def test_fsm_breakeven_step():
    """breakeven_at_r: at +1R the protective stop parks at entry — a full
    reversal then exits at ~breakeven instead of -1R."""
    cfg = TradingConfig()
    cfg.breakeven_at_r = 1.0
    cfg.rr_target = 4.0          # keep the 2R partial out of this test
    pm = _pm(cfg)
    sig = TradeSignal(Side.LONG, "T", 80, stop_price=98, entry_hint=100)
    assert pm.try_open(sig, 100, atr_val=2.0, ts=0)          # 1R = $2/px
    pm.manage(_c(60000, 100, 102.5, 99.5, 102), 2.0)         # tags +1R (102)
    p = pm.pos
    assert p.state.value == "OPEN" and p.trail_price == 100.0
    pm.manage(_c(120000, 102, 102.2, 97, 97.5), 2.0)         # full reversal
    assert p.state.value == "CLOSED"
    assert abs(p.realized_pnl_usd) < p.initial_risk_usd * 0.2  # ~breakeven
    # off by default: same path without the knob loses the full 1R
    pm2 = _pm(TradingConfig())
    assert pm2.try_open(sig, 100, atr_val=2.0, ts=0)
    pm2.manage(_c(60000, 100, 102.5, 99.5, 102), 2.0)
    assert pm2.pos.trail_price is None
    print("ok  breakeven step (1R -> stop to entry, default off)")


if __name__ == "__main__":
    test_indicators()
    test_sizing()
    test_symbol_leverage_caps()
    test_risk_gate()
    test_risk_caps_follow_compounded_equity()
    test_fsm_stop_loss()
    test_fsm_partial_then_trail()
    test_fsm_breakeven_step()
    test_fsm_pyramiding()
    test_backtest_replay()
    test_candles_export()
    test_state_persistence()
    print("\nall engine tests passed ✅")
