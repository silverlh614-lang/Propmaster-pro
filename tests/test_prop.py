"""Offline tests for the prop desk (challenge accounts, rule engine, payouts)
— no network. Run:  python -m tests.test_prop"""
from __future__ import annotations

import os
import tempfile

# isolate prop/trading state files before importing the package
_tmp = tempfile.mkdtemp(prefix="prop-test-")
os.environ["DATA_DIR"] = _tmp

from app.prop.account import (BREACH_DAILY, BREACH_MAX_DD, FAILED, FUNDED,  # noqa: E402
                              TARGET_REACHED, ChallengeAccount)
from app.prop.desk import PropDesk                        # noqa: E402
from app.prop.plans import PLANS, catalog, evaluation_fee  # noqa: E402
from app.prop.store import PayoutStore, PropStore          # noqa: E402
from app.trading.config import TradingConfig           # noqa: E402
from app.trading.risk import RiskManager        # noqa: E402
from app.trading.store import BotState, Journal      # noqa: E402

DAY1 = 1_750_000_000.0            # fixed UTC timestamps for day-anchor tests
DAY2 = DAY1 + 86_400.0


class _Ledger:
    """Minimal AccountLedger stand-in (equity read + set write-through)."""

    def __init__(self, eq: float = 0.0):
        self.equity = eq

    def set(self, v: float) -> None:
        self.equity = float(v)


def _acct(plan="1step_classic", size=10_000.0) -> ChallengeAccount:
    return ChallengeAccount(id="t1", plan_key=plan, size=size)


# ------------------------------------------------------------- catalog

def test_catalog():
    c = catalog()
    assert len(c["plans"]) == 4 and c["min_payout_usd"] == 50.0
    for p in c["plans"]:
        assert p["max_dd_pct"] > 0 and p["daily_loss_pct"] > 0
        assert len(p["phase_targets"]) == p["steps"]
        assert all(f > 0 for f in p["fees"].values())
    # 2-step is the only trailing-DD plan; 1-steps are static
    assert PLANS["2step_classic"].dd_mode == "trailing"
    assert all(PLANS[k].dd_mode == "static"
               for k in ("1step_classic", "1step_pro", "1step_turbo"))
    # published fee table wins; Classic caps at $100K so no $200K tier
    assert evaluation_fee(PLANS["1step_classic"], 10_000) == 110.0
    assert evaluation_fee(PLANS["1step_turbo"], 200_000) == 1_199.0
    classic = next(p for p in c["plans"] if p["key"] == "1step_classic")
    assert "200000" not in classic["fees"]
    print("ok  plan catalog")


# ---------------------------------------------------------- rule engine

def test_static_max_dd_breach():
    a = _acct()                                  # 10k, static DD 6% -> floor 9400
    assert a.dd_floor() == 9400.0
    assert a.evaluate(9401.0, 9401.0, True, DAY1) is None
    # anchor re-set so the daily rule (-3% of 9401) doesn't fire first
    assert a.evaluate(9400.0, 9400.0, True, DAY1) == BREACH_MAX_DD
    print("ok  static max-DD floor is fixed at size*(1-6%)")


def test_daily_loss_breach_and_rollover():
    a = _acct()                                  # 1-Step Classic: daily 4%
    assert a.evaluate(10_000.0, 10_000.0, True, DAY1) is None
    assert a.daily_floor() == 9600.0
    # breach on EQUITY even though the anchor came from balance
    assert a.evaluate(9600.0, 9900.0, False, DAY1) == BREACH_DAILY
    # same equity next anchor day (00:30 UTC boundary): re-bases, no breach
    b = _acct()
    assert b.evaluate(10_000.0, 10_000.0, True, DAY1) is None
    assert b.evaluate(9700.0, 9700.0, True, DAY1) is None     # -3.0% ok
    assert b.evaluate(9700.0, 9700.0, True, DAY2) is None     # new day anchors 9700
    assert b.daily_floor() == 9700.0 * 0.96
    print("ok  daily loss anchors on balance at the 00:30 UTC rollover")


def test_trailing_dd_ratchets_with_highwater():
    a = _acct("2step_classic")                   # trailing: 8% OF STARTING size
    assert a.dd_floor() == 9200.0
    assert a.evaluate(11_000.0, 11_000.0, False, DAY1) is None
    assert a.dd_floor() == 10_200.0              # 11000 - (8% of 10000)
    assert a.evaluate(10_150.0, 10_150.0, False, DAY1) == BREACH_MAX_DD
    print("ok  trailing DD floor = balance high-water minus the fixed budget")


def test_target_needs_flat_and_realized_balance():
    a = _acct()                                  # target 10% -> 11000
    # floating gain (not flat) never advances a phase
    assert a.evaluate(11_500.0, 10_000.0, False, DAY1) is None
    # flat but balance below target: no event either (DAY2 re-anchors the
    # daily rule so the pullback from the floating high isn't a daily breach)
    assert a.evaluate(10_990.0, 10_990.0, True, DAY2) is None
    assert a.evaluate(11_000.0, 11_000.0, True, DAY2) == TARGET_REACHED
    print("ok  targets judged on realized balance, only while flat")


# ------------------------------------------------------------- desk

def _desk(ledger=None, on_breach=None):
    """Fresh desk on a WIPED store — tests share one DATA_DIR."""
    store, pay = PropStore(), PayoutStore()
    store.save({"active_id": None, "accounts": {}})
    return PropDesk(store, pay, ledger=ledger, on_breach=on_breach)


def test_desk_purchase_and_one_active():
    led = _Ledger()
    d = _desk(led)
    r = d.buy_challenge("1step_classic", 10_000)
    assert r["ok"] and led.equity == 10_000.0
    assert d.buy_challenge("1step_classic", 5_000)["ok"] is False  # one at a time
    assert d.buy_challenge("nope", 10_000)["ok"] is False
    assert d.buy_challenge("1step_classic", 123)["ok"] is False
    d2 = _desk(_Ledger())
    assert d2.buy_challenge("1step_classic", 200_000)["ok"] is False  # caps at 100K
    assert d2.buy_challenge("1step_turbo", 200_000)["ok"] is True
    print("ok  purchase seeds the ledger; one active account; size caps enforced")


def test_desk_two_step_progression_to_funded():
    led = _Ledger()
    d = _desk(led)
    d.buy_challenge("2step_classic", 10_000)
    # phase 1 target 5% -> 10500; balance resets each phase
    led.set(10_500.0)
    r = d.on_mark(10_500.0, 10_500.0, True, DAY1)
    assert r and r["event"] == TARGET_REACHED and d.active().phase == 2
    assert led.equity == 10_000.0                # fresh stake for phase 2
    led.set(11_000.0)                            # phase 2 target 10%
    r = d.on_mark(11_000.0, 11_000.0, True, DAY1)
    assert r and d.active().status == FUNDED and led.equity == 10_000.0
    assert d.active().target_balance() is None   # funded: no profit target
    print("ok  2-step: phase 1 -> phase 2 -> FUNDED, stake resets each step")


def test_desk_breach_blocks_entries_and_allows_rebuy():
    led = _Ledger()
    hits = []
    d = _desk(led, on_breach=lambda why: hits.append(why))
    d.buy_challenge("1step_turbo", 10_000)       # static DD 3% -> floor 9700
    r = d.on_mark(9_650.0, 9_650.0, True, DAY1)
    assert r and r["event"] == BREACH_MAX_DD and hits
    assert d.active().status == FAILED
    ok, why = d.entries_allowed()
    assert not ok and "failed" in why
    # the risk gate's single permission point relays the refusal
    risk = RiskManager(TradingConfig(), Journal(), BotState())
    risk.attach_prop(d)
    ok, why = risk.allow_entry(0, 0.0, 1.0, equity_usd=10_000)
    assert not ok and why.startswith("prop:")
    # terminal: a new attempt is a NEW purchase
    assert d.buy_challenge("1step_classic", 10_000)["ok"]
    assert d.entries_allowed()[0]
    print("ok  breach is terminal, blocks the risk gate, re-buy starts fresh")


def test_desk_payouts():
    led = _Ledger()
    d = _desk(led)
    d.buy_challenge("1step_classic", 10_000)
    led.set(11_000.0)
    d.on_mark(11_000.0, 11_000.0, True, DAY1)    # -> FUNDED, ledger 10000
    assert d.request_payout(500)["ok"] is False  # no profit yet
    led.set(10_800.0)                            # +800 realized profit
    assert d.request_payout(49)["ok"] is False   # min $50
    assert d.request_payout(900)["ok"] is False  # > profit
    r = d.request_payout(500)
    # 80% split + first-payout evaluation-fee refund ($110 for Classic 10K)
    assert r["ok"] and r["fee_refund_usd"] == 110.0
    assert r["trader_usd"] == 500 * 0.8 + 110.0
    assert led.equity == 10_300.0
    assert d.active().withdrawn_usd == 500.0
    # evaluation accounts can never withdraw
    d2 = _desk(_Ledger())
    d2.buy_challenge("1step_classic", 10_000)
    assert d2.request_payout(100)["ok"] is False
    print("ok  payouts: funded-only, min $50, profit-capped, split applied")


def test_split_upgrade_and_fee_refund():
    from app.prop.plans import SPLIT_UPGRADE_PCT
    # +20% fee on the published price
    assert evaluation_fee(PLANS["1step_classic"], 10_000, split_upgrade=True) == 132.0
    led = _Ledger()
    d = _desk(led)
    r = d.buy_challenge("1step_classic", 10_000, split_upgrade=True)
    assert r["ok"] and r["account"]["profit_split_pct"] == SPLIT_UPGRADE_PCT
    assert r["account"]["fee_paid"] == 132.0
    led.set(11_000.0)
    d.on_mark(11_000.0, 11_000.0, True, DAY1)    # -> FUNDED, ledger 10000
    led.set(10_800.0)
    # first payout: 90% split + full evaluation-fee refund
    p1 = d.request_payout(500)
    assert p1["ok"] and p1["split_pct"] == 90.0
    assert p1["fee_refund_usd"] == 132.0 and p1["trader_usd"] == 582.0
    # second payout: split only, refund is once per account
    p2 = d.request_payout(100)
    assert p2["ok"] and p2["fee_refund_usd"] == 0.0 and p2["trader_usd"] == 90.0
    assert d.active().fee_refunded is True
    # catalog advertises the upgrade
    c = catalog()
    assert c["split_upgrade"] == {"split_pct": 90.0, "fee_mult": 1.2}
    print("ok  90% split upgrade (+20% fee) and first-payout fee refund")


def _row(ts_s, event, risk=None, result=""):
    import datetime as dt
    ts = dt.datetime.fromtimestamp(ts_s, dt.timezone.utc).isoformat(timespec="seconds")
    return {"ts": ts, "event": event, "result": result,
            "risk_usd": ("" if risk is None else str(risk))}


def test_conduct_monitor():
    from app.prop import conduct
    T = DAY1
    # martingale: loss -> x2 risk -> loss -> x2 risk (2 escalations) => flag
    rows = [
        _row(T + 0,    "OPEN",  risk=100),
        _row(T + 600,  "CLOSE", result="LOSS"),
        _row(T + 1200, "OPEN",  risk=200),
        _row(T + 1800, "CLOSE", result="LOSS"),
        _row(T + 2400, "OPEN",  risk=400),
    ]
    v = conduct.scan(rows, account_size=10_000, daily_loss_pct=100.0)
    assert [x["kind"] for x in v] == [conduct.MARTINGALE], v
    # a WIN in between resets the streak
    rows[3] = _row(T + 1800, "CLOSE", result="WIN")
    assert conduct.scan(rows, 10_000, 100.0) == []
    # oversize: single risk above the whole daily budget (4% of 10k = 400)
    v = conduct.scan([_row(T, "OPEN", risk=500)], 10_000, 4.0)
    assert v and v[0]["kind"] == conduct.OVERSIZE
    assert conduct.scan([_row(T, "OPEN", risk=399)], 10_000, 4.0) == []
    # revenge: loss -> instant re-entry, three times in a row
    rows = [_row(T, "OPEN", risk=100)]
    t = T
    for _ in range(3):
        rows += [_row(t + 60, "CLOSE", result="LOSS"),
                 _row(t + 120, "OPEN", risk=100)]
        t += 120
    kinds = {x["kind"] for x in conduct.scan(rows, 10_000, 100.0)}
    assert conduct.REVENGE in kinds, kinds
    # slow, disciplined re-entries (gap > cooldown) never flag
    rows = [_row(T, "OPEN", risk=100)]
    t = T
    for _ in range(3):
        rows += [_row(t + 60, "CLOSE", result="LOSS"),
                 _row(t + 60 + 900, "OPEN", risk=100)]
        t += 960
    assert conduct.scan(rows, 10_000, 100.0) == []

    # desk integration: warn-only by default (records, account survives),
    # enforce mode terminates
    led = _Ledger()
    d = _desk(led)
    d.buy_challenge("1step_classic", 10_000)
    mart = [
        _row(T + 0,    "OPEN",  risk=100),
        _row(T + 600,  "CLOSE", result="LOSS"),
        _row(T + 1200, "OPEN",  risk=200),
        _row(T + 1800, "CLOSE", result="LOSS"),
        _row(T + 2400, "OPEN",  risk=400),
    ]
    fresh = d.check_conduct(mart)
    assert len(fresh) == 1 and d.active().status == "evaluation"
    assert d.check_conduct(mart) == []          # idempotent: no re-record
    assert len(d.active().violations) == 1
    os.environ["PROP_CONDUCT_ENFORCE"] = "1"
    try:
        d2 = _desk(_Ledger())
        d2.buy_challenge("1step_classic", 10_000)
        d2.check_conduct(mart)
        assert d2.active().status == FAILED
        assert "conduct" in d2.active().breach_reason
    finally:
        del os.environ["PROP_CONDUCT_ENFORCE"]
    print("ok  conduct monitor (martingale/oversize/revenge, warn vs enforce)")


def test_funded_scaling():
    led = _Ledger()
    d = _desk(led)
    d.buy_challenge("1step_classic", 10_000)
    led.set(11_000.0)
    d.on_mark(11_000.0, 11_000.0, True, DAY1)    # -> FUNDED, ledger 10000
    # rung 1 milestone: 2 payouts AND withdrawn >= 10% of size ($1000)
    led.set(10_600.0)
    r1 = d.request_payout(500)
    assert r1["ok"] and "scaled_to" not in r1    # 1 payout, $500 — not yet
    a = d.active()
    assert a.payouts_since_scale == 1 and a.withdrawn_since_scale == 500.0
    led.set(led.equity + 500)                    # trade more profit
    r2 = d.request_payout(500)
    assert r2["ok"] and r2["scaled_to"] == 20_000.0
    a = d.active()
    assert a.size == 20_000.0 and a.scale_level == 1
    assert led.equity == 20_000.0                # fresh stake at the new rung
    assert a.highwater == 20_000.0 and a.dd_floor() == 20_000.0 * 0.94
    assert a.payouts_since_scale == 0 and a.withdrawn_since_scale == 0.0
    snap = a.snapshot()
    assert snap["scale"]["level"] == 1 and snap["scale"]["next_size"] == 40_000.0
    # cap: PROP_SCALE_MAX stops the ladder
    os.environ["PROP_SCALE_MAX"] = "20000"
    try:
        led.set(22_000.0)
        p = d.request_payout(1_000)
        assert p["ok"] and "scaled_to" not in p
        p = d.request_payout(1_000)
        assert p["ok"] and "scaled_to" not in p  # qualified but capped
        assert d.active().size == 20_000.0
        assert d.active().snapshot()["scale"]["next_size"] is None
    finally:
        del os.environ["PROP_SCALE_MAX"]
    print("ok  funded scaling (2 payouts + 10% withdrawn -> x2, capped)")


def test_prop_budget_sizing_and_gates():
    """복리단타 고정비율 기각 — 리스크는 잔여 프롭 예산의 분율에서 나오고,
    손실이 쌓이면 자동 축소되며, 일일 규율 게이트가 진입을 막는다."""
    from app.trading.config import SYMBOL_SPECS, TradingConfig
    from app.trading.execution.position import PositionManager
    from app.trading.models import Side, TradeSignal
    from app.trading.risk import RiskManager
    from app.trading.store import BotState, Journal

    cfg = TradingConfig()
    assert cfg.prop_mode and cfg.pyramid_enabled is False   # 복리 애드업 기각
    led = _Ledger(10_000.0)
    d = _desk(led)
    d.buy_challenge("1step_classic", 10_000)     # daily 4% / DD 6% static
    d.on_mark(10_000.0, 10_000.0, True, DAY1)    # anchor the day at 10000

    risk = RiskManager(cfg, Journal(), BotState())
    risk.attach_prop(d)
    pm = PositionManager(SYMBOL_SPECS["BTC"], cfg, risk, Journal(),
                         "paper", "t", ledger=led)
    sig = TradeSignal(Side.LONG, "T", 70, stop_price=99_000, entry_hint=100_000)
    # fresh account: daily room 400 * 25% = 100, DD room 600 * 10% = 60 -> $60
    assert pm.try_open(sig, 100_000, 500.0, DAY1)
    assert abs(pm.pos.initial_risk_usd - 60.0) < 1e-6

    # after a $200 losing day the rooms shrink -> risk shrinks automatically
    led2 = _Ledger(9_800.0)
    d2 = _desk(led2)
    d2.buy_challenge("1step_classic", 10_000)
    d2.on_mark(10_000.0, 10_000.0, True, DAY1)   # day anchored at 10000
    led2.set(9_800.0)
    risk2 = RiskManager(cfg, Journal(), BotState())
    risk2.attach_prop(d2)
    pm2 = PositionManager(SYMBOL_SPECS["BTC"], cfg, risk2, Journal(),
                          "paper", "t", ledger=led2)
    assert pm2.try_open(sig, 100_000, 500.0, DAY1)
    # daily room 200 * 25% = 50, DD room 400 * 10% = 40 -> $40
    assert abs(pm2.pos.initial_risk_usd - 40.0) < 1e-6

    # budget guard: open risk total may not exceed 50% of remaining daily room
    ok, why = risk2.allow_entry(0, 80.0, 40.0, equity_usd=9_800)
    assert not ok and "prop budget guard" in why  # 120 > 200 * 0.5

    # daily stop: 3 losses today freeze entries for the day
    j = Journal()
    for _ in range(3):
        j.append({"symbol": "BTC", "mode": "paper", "strategy": "t",
                  "event": "CLOSE", "result": "LOSS", "pnl_usd": -10})
    risk3 = RiskManager(cfg, j, BotState())
    ok, why = risk3.allow_entry(0, 0.0, 10.0, equity_usd=10_000)
    assert not ok and "daily_stop_after_losses" in why
    print("ok  prop budget sizing (shrinks with losses) + daily discipline gates")


def test_desk_persistence_roundtrip():
    led = _Ledger()
    store, pay = PropStore(), PayoutStore()
    store.save({"active_id": None, "accounts": {}})
    d = PropDesk(store, pay, ledger=led)
    d.buy_challenge("2step_classic", 25_000)
    d.on_mark(26_250.0, 26_250.0, True, DAY1)    # phase 1 passed
    d2 = PropDesk(store, pay, ledger=led)        # reload from disk
    a = d2.active()
    assert a is not None and a.phase == 2 and a.plan_key == "2step_classic"
    assert a.highwater == 25_000.0               # stake reset persisted
    print("ok  desk state survives a restart (accounts + active id)")


if __name__ == "__main__":
    test_catalog()
    test_static_max_dd_breach()
    test_daily_loss_breach_and_rollover()
    test_trailing_dd_ratchets_with_highwater()
    test_target_needs_flat_and_realized_balance()
    test_desk_purchase_and_one_active()
    test_desk_two_step_progression_to_funded()
    test_desk_breach_blocks_entries_and_allows_rebuy()
    test_desk_payouts()
    test_split_upgrade_and_fee_refund()
    test_conduct_monitor()
    test_funded_scaling()
    test_prop_budget_sizing_and_gates()
    test_desk_persistence_roundtrip()
    print("\nall prop tests passed ✅")
