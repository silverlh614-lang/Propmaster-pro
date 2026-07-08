"""@responsibility 챌린지 몬테카를로 — 승률·R·리스크 가정으로 플랜별 통과 확률·브리치 원인·수수료 효율 추정

Challenge pass-probability Monte Carlo (prop-ev blueprint, stdlib +
numpy). Given a trade profile (win rate, avg win in R, trades/day) it
replays thousands of synthetic evaluations against a plan's actual rules
— budget sizing, daily loss, static/trailing DD, phase targets, the
3-loss daily stop — and reports what the closed-form gambler's-ruin
can't see: how discrete sizing and the DAILY limit change P(pass).

sizing="budget" mirrors the live engine (risk = min(daily_room * frac,
dd_room * frac), capped at risk_cap_pct of size) so the answer applies
to THIS bot — with atomic flat trades that sizing can NEVER touch a
floor, so weak profiles die by TIMEOUT, not breach. sizing="fixed"
(classic risk_cap_pct of size every trade) is the comparison mode where
breaches do happen — the difference is the argument for budget sizing.
Outputs per plan: P(pass), P(breach daily/DD), P(timeout), median
trades/days to pass, and fee_per_funded = fee / P(pass) — the real
price of one funded account. Simulation only, not investment advice.
"""
from __future__ import annotations

import numpy as np

from .plans import DD_TRAILING, PLANS, evaluation_fee

DEFAULTS = dict(win_rate=0.40, avg_win_r=2.0, trades_per_day=3,
                n_sims=3000, max_days=120, seed=7)


def simulate_challenge(plan_key: str, size: float, win_rate: float,
                       avg_win_r: float, trades_per_day: int,
                       risk_daily_frac: float = 0.25,
                       risk_dd_frac: float = 0.10,
                       risk_cap_pct: float = 1.0,
                       daily_stop_after_losses: int = 3,
                       split_upgrade: bool = False, sizing: str = "budget",
                       n_sims: int = 3000, max_days: int = 120,
                       seed: int = 7) -> dict:
    """Monte Carlo one plan. Trades are atomic (-1R loss / +avg_win_r win,
    fees folded into the R profile); breach checks run trade-by-trade on
    balance (flat between trades, so balance == equity)."""
    plan = PLANS[plan_key]
    rng = np.random.default_rng(seed)
    dd_budget = size * plan.max_dd_pct / 100.0

    passed = np.zeros(n_sims, dtype=bool)
    breach_daily = 0
    breach_dd = 0
    trades_used = np.zeros(n_sims)
    days_used = np.zeros(n_sims)

    for s in range(n_sims):
        phase, bal, hw = 1, float(size), float(size)
        alive, done = True, False
        n_trades = 0
        for day in range(max_days):
            anchor = bal                       # 00:30 UTC re-anchor on balance
            daily_floor = anchor * (1.0 - plan.daily_loss_pct / 100.0)
            losses_today = 0
            for _ in range(trades_per_day):
                if losses_today >= daily_stop_after_losses:
                    break                      # engine discipline gate
                dd_floor = ((hw - dd_budget) if plan.dd_mode == DD_TRAILING
                            else size - dd_budget)
                if sizing == "fixed":
                    risk = size * risk_cap_pct / 100.0
                else:
                    risk = min((bal - daily_floor) * risk_daily_frac,
                               (bal - dd_floor) * risk_dd_frac,
                               size * risk_cap_pct / 100.0)
                if risk <= 0:
                    break                      # budget exhausted — wait a day
                if rng.random() < win_rate:
                    bal += risk * avg_win_r
                else:
                    bal -= risk
                    losses_today += 1
                n_trades += 1
                hw = max(hw, bal)
                if bal <= dd_floor + 1e-9:
                    alive, breach_dd = False, breach_dd + 1
                    break
                if bal <= daily_floor + 1e-9:
                    alive, breach_daily = False, breach_daily + 1
                    break
                target = size * (1.0 + plan.phase_targets[phase - 1] / 100.0)
                if bal >= target:
                    if phase < plan.steps:
                        phase += 1
                        bal, hw = float(size), float(size)   # fresh stake
                        anchor = bal
                        daily_floor = anchor * (1.0 - plan.daily_loss_pct / 100.0)
                    else:
                        done = True
                    break
            if not alive or done:
                break
        passed[s] = done
        trades_used[s] = n_trades
        days_used[s] = day + 1

    p_pass = float(passed.mean())
    fee = evaluation_fee(plan, size, split_upgrade)
    pass_days = days_used[passed]
    return {
        "plan": plan_key, "size": size,
        "assumptions": {"win_rate": win_rate, "avg_win_r": avg_win_r,
                        "trades_per_day": trades_per_day, "sizing": sizing,
                        "risk_daily_frac": risk_daily_frac,
                        "risk_dd_frac": risk_dd_frac,
                        "risk_cap_pct": risk_cap_pct,
                        "n_sims": n_sims, "max_days": max_days},
        "p_pass": round(p_pass, 4),
        "p_breach_daily": round(breach_daily / n_sims, 4),
        "p_breach_dd": round(breach_dd / n_sims, 4),
        "p_timeout": round(1.0 - p_pass - (breach_daily + breach_dd) / n_sims, 4),
        "median_days_to_pass": (float(np.median(pass_days)) if p_pass else None),
        "median_trades": float(np.median(trades_used)),
        "fee_usd": fee,
        "fee_per_funded_usd": (round(fee / p_pass, 2) if p_pass > 0 else None),
    }


def sweep_plans(size: float, win_rate: float, avg_win_r: float,
                trades_per_day: int, **kw) -> dict:
    """Same trade profile against every plan (respecting size caps) —
    'which challenge should this bot buy' in one table."""
    rows = [simulate_challenge(k, size, win_rate, avg_win_r,
                               trades_per_day, **kw)
            for k, p in PLANS.items() if size <= p.max_size]
    rows.sort(key=lambda r: (r["fee_per_funded_usd"] is None,
                             r["fee_per_funded_usd"]))
    return {"size": size, "results": rows,
            "best": rows[0]["plan"] if rows else None}
