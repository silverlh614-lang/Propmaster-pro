"""@responsibility 챌린지 계좌 룰 엔진 — 일일손실·최대DD(정적/추적)·수익목표를 equity 기준으로 판정

Challenge account + rule engine. One ChallengeAccount owns every number
the prop rules are judged against: the fixed account size, the equity
high-water mark (trailing DD), the UTC day anchor (daily loss) and the
current evaluation phase.

Judgement basis (Breakout official mechanics):
  - Limits are CALCULATED FROM BALANCE (closed PnL): the daily anchor and
    the trailing high-water track realized balance, and the trailing floor
    is highest balance minus (max_dd% OF THE STARTING balance).
  - BREACHES are CHECKED AGAINST EQUITY (balance + floating PnL) so an
    open position can terminate the account intrabar — evaluate() must be
    fed the marked-to-market equity.
  - The daily budget re-anchors once per day at 00:30 UTC from the balance
    at that moment (limit = anchor * (1 - daily%), valid 24h).
  - PHASE TARGETS are checked on realized BALANCE and only while flat, so
    a floating gain that later evaporates can never advance a phase.
Breach is terminal: the account flips to FAILED and stays there. A new
evaluation means a new account (new purchase), never a reset.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import asdict, dataclass

from .plans import DD_TRAILING, PLANS, PropPlan

DAILY_ANCHOR_UTC_SEC = 30 * 60    # daily budget re-anchors at 00:30 UTC

# account status
EVALUATION = "evaluation"
FUNDED = "funded"
FAILED = "failed"

# evaluate() event vocabulary
BREACH_MAX_DD = "breach_max_dd"
BREACH_DAILY = "breach_daily_loss"
TARGET_REACHED = "target_reached"


def utc_day(ts: float | None = None) -> str:
    """Anchor day label — the boundary sits at 00:30 UTC, not midnight."""
    if ts is None:
        ts = dt.datetime.now(dt.timezone.utc).timestamp()
    d = dt.datetime.fromtimestamp(ts - DAILY_ANCHOR_UTC_SEC, dt.timezone.utc)
    return d.strftime("%Y-%m-%d")


@dataclass
class ChallengeAccount:
    id: str
    plan_key: str
    size: float                      # account size, fixed at purchase
    status: str = EVALUATION         # evaluation | funded | failed
    phase: int = 1                   # 1-based evaluation phase
    highwater: float = 0.0           # BALANCE high-water mark (trailing DD)
    day: str = ""                    # anchor-day label (00:30 UTC boundary)
    day_anchor: float = 0.0          # balance at the day's first mark
    fee_paid: float = 0.0
    breach_reason: str = ""
    withdrawn_usd: float = 0.0       # lifetime payouts (gross, funded only)
    created_ts: float = 0.0

    def __post_init__(self):
        if self.highwater <= 0:
            self.highwater = self.size
        if self.day_anchor <= 0:
            self.day_anchor = self.size

    # ------------------------------------------------------------- plan

    @property
    def plan(self) -> PropPlan:
        return PLANS[self.plan_key]

    # ------------------------------------------------------------ floors

    def dd_floor(self) -> float:
        """Equity at/below which the max-drawdown rule is breached.
        Static: starting balance - X%. Trailing: highest BALANCE minus
        (X% of the STARTING balance) — the budget is fixed, the floor
        follows the balance high-water mark."""
        budget = self.size * self.plan.max_dd_pct / 100.0
        base = (self.highwater if self.plan.dd_mode == DD_TRAILING
                else self.size)
        return round(base - budget, 8)

    def daily_floor(self) -> float:
        """Equity at/below which today's daily-loss rule is breached."""
        return round(self.day_anchor * (1.0 - self.plan.daily_loss_pct / 100.0), 8)

    def target_balance(self) -> float | None:
        """Balance that completes the current phase (None once funded)."""
        if self.status != EVALUATION:
            return None
        pct = self.plan.phase_targets[self.phase - 1]
        return round(self.size * (1.0 + pct / 100.0), 8)

    # ------------------------------------------------------------- rules

    def evaluate(self, equity_mark: float, balance: float, flat: bool,
                 ts: float | None = None) -> str | None:
        """Judge one mark-to-market tick. Returns an event constant or None.

        equity_mark = balance + unrealized (breach basis);
        balance     = realized ledger equity (target basis);
        flat        = no open position (targets only fire while flat)."""
        if self.status == FAILED:
            return None
        day = utc_day(ts)
        if day != self.day:                     # 00:30 UTC rollover re-anchors
            self.day = day
            self.day_anchor = balance           # limits computed from BALANCE
        if balance > self.highwater:
            self.highwater = balance

        if equity_mark <= self.dd_floor():
            return BREACH_MAX_DD
        if equity_mark <= self.daily_floor():
            return BREACH_DAILY
        tgt = self.target_balance()
        if tgt is not None and flat and balance >= tgt:
            return TARGET_REACHED
        return None

    def fail(self, reason: str) -> None:
        self.status = FAILED
        self.breach_reason = reason

    # ------------------------------------------------------------- view

    def snapshot(self, equity_mark: float | None = None,
                 balance: float | None = None) -> dict:
        d = asdict(self)
        d["plan"] = self.plan.as_dict()
        d["dd_floor"] = self.dd_floor()
        d["daily_floor"] = self.daily_floor()
        d["target_balance"] = self.target_balance()
        if equity_mark is not None:
            d["equity_mark"] = round(equity_mark, 4)
            d["room_to_dd_floor"] = round(equity_mark - self.dd_floor(), 4)
            d["room_to_daily_floor"] = round(equity_mark - self.daily_floor(), 4)
        if balance is not None:
            d["balance"] = round(balance, 4)
            tgt = self.target_balance()
            if tgt is not None:
                d["target_progress_pct"] = round(
                    max(0.0, (balance - self.size)) / (tgt - self.size) * 100.0, 2)
        return d

    # ------------------------------------------------------ persistence

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "ChallengeAccount":
        known = {f for f in cls.__dataclass_fields__}  # tolerate extra keys
        return cls(**{k: v for k, v in d.items() if k in known})
