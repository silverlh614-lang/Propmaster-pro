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

The desk never places or blocks orders itself — RiskManager consults
entries_allowed() inside allow_entry() (the single permission point).
"""
from __future__ import annotations

import time

from .account import (BREACH_DAILY, BREACH_MAX_DD, EVALUATION, FAILED, FUNDED,
                      TARGET_REACHED, ChallengeAccount)
from .plans import (ACCOUNT_SIZES, PLANS, catalog, evaluation_fee,
                    min_payout_usd)
from .store import PayoutStore, PropStore

BREACH_LABEL = {BREACH_MAX_DD: "max drawdown", BREACH_DAILY: "daily loss"}


class PropDesk:
    def __init__(self, store: PropStore | None = None,
                 payouts: PayoutStore | None = None,
                 ledger=None, on_breach=None):
        self.store = store or PropStore()
        self.payouts = payouts or PayoutStore()
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

    def buy_challenge(self, plan_key: str, size: float) -> dict:
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
            fee_paid=evaluation_fee(plan, size), created_ts=time.time())
        self.accounts[acct_id] = acct
        self.active_id = acct_id
        self._reset_stake(acct)
        self._persist()
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

    def entries_allowed(self) -> tuple[bool, str]:
        """Consulted by RiskManager.allow_entry (single permission
        point). No active account = engine runs standalone (allowed)."""
        acct = self.active()
        if acct is None:
            return True, ""
        if acct.status == FAILED:
            return False, f"account failed ({acct.breach_reason})"
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
        split = acct.plan.profit_split_pct
        trader_usd = round(amount * split / 100.0, 2)
        self.ledger.set(self.ledger.equity - amount)
        acct.withdrawn_usd = round(acct.withdrawn_usd + amount, 2)
        # payout resets the daily anchor baseline down with the balance so a
        # withdrawal is never judged as a "loss" by the daily rule
        acct.day_anchor = max(acct.dd_floor(), acct.day_anchor - amount)
        self._persist()
        rec = {"ts": time.time(), "account": acct.id, "amount_usd": amount,
               "split_pct": split, "trader_usd": trader_usd,
               "currency": "USDC (simulated)", "status": "paid"}
        self.payouts.append(rec)
        return {"ok": True, **rec, "balance_after": round(self.ledger.equity, 2)}

    # ------------------------------------------------------------- views

    def status(self, equity_mark: float | None = None,
               balance: float | None = None) -> dict:
        acct = self.active()
        return {
            "active": (acct.snapshot(equity_mark, balance) if acct else None),
            "accounts": [a.snapshot() for a in self.accounts.values()],
            "payouts": self.payouts.load()[-20:][::-1],
        }

    @staticmethod
    def catalog() -> dict:
        return catalog()
