"""@responsibility 프롭 챌린지 플랜 카탈로그 SSOT — Breakout 공개 규칙 기반 평가 규칙·계좌 크기·수수료 정의

Challenge plan catalog, calibrated to Breakout Prop's published structure
(breakoutprop.com, 2025-2026, Kraken-owned). Each plan bundles what a
trader signs up for: per-phase profit targets, the max-drawdown budget and
its mode, the daily-loss budget and the funded profit split.

Rule mechanics (official docs):
  - Limits are CALCULATED FROM BALANCE (closed PnL); breach is CHECKED
    AGAINST EQUITY (incl. floating PnL) — enforced in account.py.
  - Static DD floor = starting balance - X% (never moves).
    Trailing DD floor = highest balance - (X% OF STARTING balance).
  - Daily loss re-anchors every day at 00:30 UTC from the balance then.
  - Same rules in evaluation and funded; breach = account forfeited.
Fees are the published one-time prices where confirmed; missing tiers fall
back to fee_rate * size. Base profit split is 80%; the 90% split is a
checkout add-on (+20% fee) and the evaluation fee refunds with the first
funded payout. PROP_FEE_MULT scales all fees; PROP_MIN_PAYOUT overrides
the payout minimum (sources conflict: "no minimum" vs ~$100 after split).
"""
from __future__ import annotations

import os
from dataclasses import asdict, dataclass, field

DD_STATIC = "static"      # floor fixed from the starting balance
DD_TRAILING = "trailing"  # floor ratchets up with the balance high-water mark


@dataclass(frozen=True)
class PropPlan:
    key: str                      # registry key, e.g. "1step_classic"
    name: str                     # display name
    steps: int                    # number of evaluation phases (1 or 2)
    phase_targets: tuple          # profit target % per phase, e.g. (5.0, 10.0)
    max_dd_pct: float             # max drawdown budget (% of starting balance)
    dd_mode: str                  # DD_STATIC | DD_TRAILING
    daily_loss_pct: float         # daily budget (% of the 00:30 UTC balance)
    profit_split_pct: float       # funded: trader's share of withdrawn profit
    max_size: float               # largest account size this plan sells
    fee_table: dict = field(default_factory=dict)  # published USD fees by size
    fee_rate: float = 0.010       # fallback fee fraction for missing tiers

    def as_dict(self) -> dict:
        return asdict(self)


PLANS: dict[str, PropPlan] = {
    "1step_classic": PropPlan(
        key="1step_classic", name="1-Step Classic", steps=1,
        phase_targets=(10.0,), max_dd_pct=6.0, dd_mode=DD_STATIC,
        daily_loss_pct=4.0, profit_split_pct=80.0, max_size=100_000,
        fee_table={5_000: 55, 10_000: 110, 25_000: 275,
                   50_000: 495, 100_000: 800}, fee_rate=0.011),
    "1step_pro": PropPlan(
        key="1step_pro", name="1-Step Pro", steps=1,
        phase_targets=(12.0,), max_dd_pct=5.0, dd_mode=DD_STATIC,
        daily_loss_pct=3.0, profit_split_pct=80.0, max_size=200_000,
        fee_table={200_000: 1_399}, fee_rate=0.011),
    "1step_turbo": PropPlan(
        key="1step_turbo", name="1-Step Turbo", steps=1,
        phase_targets=(9.0,), max_dd_pct=3.0, dd_mode=DD_STATIC,
        daily_loss_pct=3.0, profit_split_pct=80.0, max_size=200_000,
        fee_table={5_000: 45, 25_000: 199, 100_000: 599,
                   200_000: 1_199}, fee_rate=0.008),
    "2step_classic": PropPlan(
        key="2step_classic", name="2-Step Classic", steps=2,
        phase_targets=(5.0, 10.0), max_dd_pct=8.0, dd_mode=DD_TRAILING,
        daily_loss_pct=5.0, profit_split_pct=80.0, max_size=100_000,
        fee_table={5_000: 50, 10_000: 100, 25_000: 250,
                   50_000: 450, 100_000: 725}, fee_rate=0.010),
}

ACCOUNT_SIZES = (5_000, 10_000, 25_000, 50_000, 100_000, 200_000)

# 90% profit-split upgrade: checkout add-on, ~+20% on the evaluation fee,
# permanent for the account's life (Breakout-style). Base split stays 80%.
SPLIT_UPGRADE_PCT = 90.0
SPLIT_UPGRADE_FEE_MULT = 1.2


def min_payout_usd() -> float:
    return float(os.getenv("PROP_MIN_PAYOUT", "50"))


def evaluation_fee(plan: PropPlan, size: float,
                   split_upgrade: bool = False) -> float:
    """One-time evaluation fee: published price when known, else the plan's
    fallback rate. The 90%-split add-on costs +20%. PROP_FEE_MULT scales
    everything (deployment knob)."""
    mult = float(os.getenv("PROP_FEE_MULT", "1.0"))
    base = plan.fee_table.get(int(size), size * plan.fee_rate)
    if split_upgrade:
        base *= SPLIT_UPGRADE_FEE_MULT
    return round(base * mult, 2)


def catalog() -> dict:
    """Plan catalog + fee table for the API/UI."""
    return {
        "sizes": list(ACCOUNT_SIZES),
        "min_payout_usd": min_payout_usd(),
        "split_upgrade": {"split_pct": SPLIT_UPGRADE_PCT,
                          "fee_mult": SPLIT_UPGRADE_FEE_MULT},
        "plans": [
            {**p.as_dict(),
             "fees": {str(s): evaluation_fee(p, s)
                      for s in ACCOUNT_SIZES if s <= p.max_size}}
            for p in PLANS.values()
        ],
    }
