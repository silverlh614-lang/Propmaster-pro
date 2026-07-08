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
    test_desk_persistence_roundtrip()
    print("\nall prop tests passed ✅")
