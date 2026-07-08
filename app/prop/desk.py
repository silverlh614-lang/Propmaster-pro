"""@responsibility 프롭 데스크 오케스트레이션 — 챌린지 구매·마크 판정·단계 승급·펀디드 전환·페이아웃 소유

Prop desk lifecycle owner. Holds the account registry (one ACTIVE account
at a time — the engine has one ledger), routes every mark-to-market tick
into the active account's rule engine, and executes the consequences:

  breach  -> account FAILED (terminal) + on_breach callback so the trading
             layer can trip its kill switch and flatten. New attempt = new
             purchase, never a reset.
  target  -> phase advance, or FUNDED on the last phase. Balance resets to
             the plan size on every transition (fresh phase = fresh stake);
             the shared AccountLedger is rewritten so the trading engine
             sizes off the prop balance from the next tick.
  payout  -> funded only, on-demand, min $50, capped at realized profit
             above size; trader receives amount * profit split.

Every purchase and payout also books the desk's side of the money into
the RevenueLedger (challenge fee, split add-on, payout spread, refund) —
the monetization record lives here because the desk is where money moves.

The desk never places or blocks orders itself — RiskManager consults
entries_allowed() inside allow_entry() (the single permission point).
"""
from __future__ import annotations

import os
import time

from . import conduct
from .account import (BREACH_DAILY, BREACH_MAX_DD, EVALUATION, FAILED, FUNDED,
                      TARGET_REACHED, ChallengeAccount)
from .plans import (ACCOUNT_SIZES, PLANS, SCALE_MIN_PAYOUTS,
                    SCALE_MIN_PROFIT_PCT, SCALE_STEP_MULT, SPLIT_UPGRADE_PCT,
                    catalog, evaluation_fee, min_payout_usd, scale_max_usd)
from .revenue import (STREAM_CHALLENGE_FEE, STREAM_FEE_REFUND,
                      STREAM_PAYOUT_SPREAD, STREAM_SPLIT_ADDON, RevenueLedger)
from .store import PayoutStore, PropStore, RevenueStore

BREACH_LABEL = {BREACH_MAX_DD: "max drawdown", BREACH_DAILY: "daily loss"}


class PropDesk:
    def __init__(self, store: PropStore | None = None,
                 payouts: PayoutStore | None = None,
                 ledger=None, on_breach=None,
                 revenue: RevenueStore | None = None):
        self.store = store or PropStore()
        self.payouts = payouts or PayoutStore()
        self.revenue = RevenueLedger(revenue)
        self.ledger = ledger          # shared AccountLedger (None in unit tests)
        self.on_breach = on_breach    # callable(reason) -> None
        blob = self.store.load()
        self.accounts: dict[str, ChallengeAccount] = {
            k: ChallengeAccount.from_dict(v)
            for k, v in blob.get("accounts", {}).items()}
        self.active_id: str | None = blob.get("active_id")

    # ------------------------------------------------------------ helpers

    def active(self) -> ChallengeAccount | None:
        return self.accounts.get(self.active_id) if self.active_id else None

    def _persist(self) -> None:
        self.store.save({
            "active_id": self.active_id,
            "accounts": {k: a.to_dict() for k, a in self.accounts.items()}})

    def _reset_stake(self, acct: ChallengeAccount) -> None:
        """Fresh phase = fresh stake: balance, high-water and daily anchor
        all restart from the plan size."""
        acct.highwater = acct.size
        acct.day_anchor = acct.size
        if self.ledger is not None:
            self.ledger.set(acct.size)

    # ----------------------------------------------------------- purchase

    def buy_challenge(self, plan_key: str, size: float,
                      split_upgrade: bool = False) -> dict:
        if plan_key not in PLANS:
            return {"ok": False, "error": f"unknown plan '{plan_key}'"}
        if size not in ACCOUNT_SIZES:
            return {"ok": False, "error": f"size must be one of {ACCOUNT_SIZES}"}
        if size > PLANS[plan_key].max_size:
            return {"ok": False,
                    "error": f"{PLANS[plan_key].name} caps at "
                             f"${PLANS[plan_key].max_size:,.0f}"}
        cur = self.active()
        if cur and cur.status != FAILED:
            return {"ok": False,
                    "error": f"account '{cur.id}' is still {cur.status} — "
                             "one active account at a time"}
        plan = PLANS[plan_key]
        acct_id = f"{plan_key}-{int(size / 1000)}k-{len(self.accounts) + 1}"
        acct = ChallengeAccount(
            id=acct_id, plan_key=plan_key, size=float(size),
            fee_paid=evaluation_fee(plan, size, split_upgrade),
            created_ts=time.time(),
            profit_split_pct=(SPLIT_UPGRADE_PCT if split_upgrade
                              else plan.profit_split_pct))
        self.accounts[acct_id] = acct
        self.active_id = acct_id
        self._reset_stake(acct)
        self._persist()
        # book the sale: base evaluation fee, plus the 90%-split add-on
        # margin as its own stream (fee_paid = base * 1.2 when upgraded)
        base_fee = evaluation_fee(plan, size, split_upgrade=False)
        self.revenue.record(STREAM_CHALLENGE_FEE, base_fee, acct_id,
                            note=plan.name, ts=acct.created_ts)
        if split_upgrade:
            self.revenue.record(STREAM_SPLIT_ADDON,
                                round(acct.fee_paid - base_fee, 2), acct_id,
                                note="90% split add-on", ts=acct.created_ts)
        return {"ok": True, "account": acct.snapshot()}

    # ---------------------------------------------------------- mark tick

    def on_mark(self, equity_mark: float, balance: float, flat: bool,
                ts: float | None = None) -> dict | None:
        """Feed one mark-to-market tick to the active account's rules and
        act on the verdict. Returns the transition (if any) for the note."""
        acct = self.active()
        if acct is None or acct.status == FAILED:
            return None
        event = acct.evaluate(equity_mark, balance, flat, ts)
        if event is None:
            self._persist()           # highwater / day anchor moved
            return None
        if event in BREACH_LABEL:
            reason = (f"{BREACH_LABEL[event]} breached: equity "
                      f"{equity_mark:.2f} <= floor "
                      f"{acct.dd_floor() if event == BREACH_MAX_DD else acct.daily_floor():.2f}")
            acct.fail(reason)
            self._persist()
            if self.on_breach is not None:
                self.on_breach(reason)
            return {"event": event, "reason": reason, "account": acct.id}
        # TARGET_REACHED — advance a phase or graduate to funded
        if acct.phase < acct.plan.steps:
            acct.phase += 1
            note = f"phase {acct.phase - 1} passed -> phase {acct.phase}"
        else:
            acct.status = FUNDED
            note = "evaluation passed -> FUNDED (company capital, no target)"
        self._reset_stake(acct)
        self._persist()
        return {"event": TARGET_REACHED, "note": note, "account": acct.id}

    def check_conduct(self, journal_rows: list[dict]) -> list[dict]:
        """Scan the trade journal for gambling-style conduct (martingale,
        oversize, revenge trading). New violations are recorded on the
        account; with PROP_CONDUCT_ENFORCE=1 a violation terminates it
        (real desks ban for this — default is record-and-warn)."""
        acct = self.active()
        if acct is None or acct.status == FAILED:
            return []
        found = conduct.scan(journal_rows, acct.size,
                             acct.plan.daily_loss_pct)
        seen = {v.get("key") for v in acct.violations}
        fresh = [v for v in found if v["key"] not in seen]
        if not fresh:
            return []
        acct.violations.extend(fresh)
        enforce = os.getenv("PROP_CONDUCT_ENFORCE", "0").strip() in ("1", "true")
        if enforce:
            reason = f"conduct violation: {fresh[0]['kind']} — {fresh[0]['detail']}"
            acct.fail(reason)
            self._persist()
            if self.on_breach is not None:
                self.on_breach(reason)
        else:
            self._persist()
        return fresh

    def risk_budget(self) -> dict | None:
        """Remaining rule-room for the sizing layer: how much equity can be
        lost before each floor. None when no live prop account governs."""
        acct = self.active()
        if acct is None or acct.status == FAILED or self.ledger is None:
            return None
        bal = self.ledger.equity
        return {"daily_room": max(0.0, bal - acct.daily_floor()),
                "dd_room": max(0.0, bal - acct.dd_floor())}

    def guard_level(self) -> dict | None:
        """Equity-guardian tiers (MT5 drawdown-guard pattern): ratio =
        remaining room / full budget, min over the daily and DD rules.
        <= soft -> warn only; <= hard -> entry lock + flatten, BEFORE the
        real floor can terminate the account. Env: PROP_GUARD_SOFT /
        PROP_GUARD_HARD (fractions; 0 disables a tier)."""
        b = self.risk_budget()
        acct = self.active()
        if b is None or acct is None:
            return None
        daily_budget = acct.day_anchor * acct.plan.daily_loss_pct / 100.0
        dd_budget = acct.size * acct.plan.max_dd_pct / 100.0
        ratio = min(
            (b["daily_room"] / daily_budget) if daily_budget > 0 else 1.0,
            (b["dd_room"] / dd_budget) if dd_budget > 0 else 1.0)
        soft = float(os.getenv("PROP_GUARD_SOFT", "0.35"))
        hard = float(os.getenv("PROP_GUARD_HARD", "0.15"))
        level = ("hard" if (hard > 0 and ratio <= hard)
                 else "soft" if (soft > 0 and ratio <= soft) else "ok")
        return {"level": level, "ratio": round(ratio, 4),
                "soft": soft, "hard": hard}

    def entries_allowed(self) -> tuple[bool, str]:
        """Consulted by RiskManager.allow_entry (single permission
        point). No active account = engine runs standalone (allowed)."""
        acct = self.active()
        if acct is None:
            return True, ""
        if acct.status == FAILED:
            return False, f"account failed ({acct.breach_reason})"
        g = self.guard_level()
        if g and g["level"] == "hard":
            return False, (f"guard buffer: remaining budget {g['ratio']:.0%} "
                           f"<= {g['hard']:.0%} — entries locked")
        return True, ""

    # ------------------------------------------------------------ payouts

    def request_payout(self, amount: float) -> dict:
        acct = self.active()
        if acct is None or acct.status != FUNDED:
            return {"ok": False, "error": "payouts require an active FUNDED account"}
        if self.ledger is None:
            return {"ok": False, "error": "no ledger attached"}
        floor_usd = min_payout_usd()
        if amount < floor_usd:
            return {"ok": False, "error": f"minimum payout is ${floor_usd:.0f}"}
        profit = self.ledger.equity - acct.size
        if amount > profit + 1e-9:
            return {"ok": False,
                    "error": f"amount ${amount:.2f} exceeds withdrawable "
                             f"profit ${max(profit, 0):.2f}"}
        split = acct.profit_split_pct
        # first funded payout refunds the evaluation fee (Breakout-style)
        refund = 0.0 if acct.fee_refunded else acct.fee_paid
        trader_usd = round(amount * split / 100.0 + refund, 2)
        self.ledger.set(self.ledger.equity - amount)
        acct.withdrawn_usd = round(acct.withdrawn_usd + amount, 2)
        acct.fee_refunded = True
        acct.payouts_since_scale += 1
        acct.withdrawn_since_scale = round(acct.withdrawn_since_scale + amount, 2)
        # payout resets the daily anchor baseline down with the balance so a
        # withdrawal is never judged as a "loss" by the daily rule
        acct.day_anchor = max(acct.dd_floor(), acct.day_anchor - amount)
        scaled_to = self._maybe_scale(acct)
        self._persist()
        rec = {"ts": time.time(), "account": acct.id, "amount_usd": amount,
               "split_pct": split, "fee_refund_usd": refund,
               "trader_usd": trader_usd,
               "currency": "USDC (simulated)", "status": "paid"}
        if scaled_to:
            rec["scaled_to"] = scaled_to
        self.payouts.append(rec)
        # book the desk's side: the split spread earns, the one-time
        # evaluation-fee refund costs (negative stream)
        self.revenue.record(STREAM_PAYOUT_SPREAD,
                            round(amount * (100.0 - split) / 100.0, 2),
                            acct.id, ts=rec["ts"])
        if refund:
            self.revenue.record(STREAM_FEE_REFUND, -refund, acct.id,
                                note="first-payout fee refund", ts=rec["ts"])
        return {"ok": True, **rec, "balance_after": round(self.ledger.equity, 2)}

    def _maybe_scale(self, acct: ChallengeAccount) -> float | None:
        """Funded scaling rung: SCALE_MIN_PAYOUTS payouts AND withdrawn
        profit >= SCALE_MIN_PROFIT_PCT of the current size double the
        account (fresh stake), capped at PROP_SCALE_MAX."""
        cap = scale_max_usd()
        if acct.status != FUNDED or acct.size >= cap:
            return None
        if acct.payouts_since_scale < SCALE_MIN_PAYOUTS:
            return None
        if acct.withdrawn_since_scale < acct.size * SCALE_MIN_PROFIT_PCT / 100.0:
            return None
        acct.size = min(acct.size * SCALE_STEP_MULT, cap)
        acct.scale_level += 1
        acct.payouts_since_scale = 0
        acct.withdrawn_since_scale = 0.0
        self._reset_stake(acct)          # new rung = fresh stake at new size
        return acct.size

    # ------------------------------------------------------------- views

    def status(self, equity_mark: float | None = None,
               balance: float | None = None) -> dict:
        acct = self.active()
        return {
            "active": (acct.snapshot(equity_mark, balance) if acct else None),
            "guard": self.guard_level(),
            "accounts": [a.snapshot() for a in self.accounts.values()],
            "payouts": self.payouts.load()[-20:][::-1],
            "revenue": self.revenue.summary(),
        }

    @staticmethod
    def catalog() -> dict:
        return catalog()
