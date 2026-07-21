"""@responsibility 프롭 데스크 REST API — /api/prop/* 플랜·챌린지 구매·계좌 상태·페이아웃

REST API for the prop desk (/api/prop/*). The desk instance is owned by
TradingManager so the challenge account, the risk gate and the shared
ledger are always the same objects the trading loop uses.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..trading.bot import MANAGER

router = APIRouter(prefix="/api/prop", tags=["prop"])


class ChallengeRequest(BaseModel):
    plan: str = "1step_turbo"        # 기본: MDL 3% · MDD 3% (Turbo)
    size: float = 10_000
    split_upgrade: bool = False      # 90% split add-on (+20% fee)


class PayoutRequest(BaseModel):
    amount: float


@router.get("/plans")
def plans():
    """Challenge plan catalog: rules, sizes and evaluation fees."""
    return MANAGER.prop.catalog()


@router.post("/challenge")
def buy_challenge(req: ChallengeRequest):
    """Buy an evaluation challenge (simulated fee). Resets the ledger to the
    account size; one active account at a time."""
    _mark, _bal, flat = MANAGER.prop_mark_inputs()
    if not flat:
        raise HTTPException(409, "close open positions before buying a challenge")
    res = MANAGER.prop.buy_challenge(req.plan, req.size, req.split_upgrade)
    if not res.get("ok"):
        raise HTTPException(409, res.get("error", "purchase failed"))
    return res


@router.get("/account")
def account():
    """Active challenge account with live floors/progress (marked to market)."""
    equity_mark, balance, _flat = MANAGER.prop_mark_inputs()
    return MANAGER.prop.status(equity_mark=equity_mark, balance=balance)


@router.post("/payout")
def payout(req: PayoutRequest):
    """On-demand payout (funded accounts only, min $50, profit split applied)."""
    res = MANAGER.prop.request_payout(req.amount)
    if not res.get("ok"):
        raise HTTPException(409, res.get("error", "payout refused"))
    return res


@router.get("/payouts")
def payouts():
    return {"payouts": MANAGER.prop.payouts.load()[::-1]}


class SimulateRequest(BaseModel):
    plan: str | None = None          # None = sweep every plan this size fits
    size: float = 10_000
    win_rate: float = 0.40           # per-trade hit rate
    avg_win_r: float = 2.0           # avg win in R (loss = -1R, fees folded in)
    trades_per_day: int = 3
    split_upgrade: bool = False
    sizing: str = "budget"           # "budget" = engine sizing | "fixed" = flat %
    n_sims: int = 3000
    seed: int = 7


@router.post("/simulate")
def simulate(req: SimulateRequest):
    """Monte Carlo pass-probability: which plan is +EV for THIS trade
    profile (engine budget sizing + daily discipline mirrored)."""
    from .plans import ACCOUNT_SIZES, PLANS
    from .simulate import simulate_challenge, sweep_plans
    if req.size not in ACCOUNT_SIZES:
        raise HTTPException(422, f"size must be one of {ACCOUNT_SIZES}")
    if not (0.0 < req.win_rate < 1.0) or req.avg_win_r <= 0:
        raise HTTPException(422, "need 0<win_rate<1 and avg_win_r>0")
    if not (1 <= req.trades_per_day <= 50):
        raise HTTPException(422, "trades_per_day must be 1..50")
    if req.sizing not in ("budget", "fixed"):
        raise HTTPException(422, "sizing must be 'budget' or 'fixed'")
    n = max(200, min(req.n_sims, 20_000))
    if req.plan is None:
        return sweep_plans(req.size, req.win_rate, req.avg_win_r,
                           req.trades_per_day, split_upgrade=req.split_upgrade,
                           sizing=req.sizing, n_sims=n, seed=req.seed)
    if req.plan not in PLANS:
        raise HTTPException(422, f"unknown plan '{req.plan}'")
    if req.size > PLANS[req.plan].max_size:
        raise HTTPException(422, f"{req.plan} caps at {PLANS[req.plan].max_size}")
    return simulate_challenge(req.plan, req.size, req.win_rate, req.avg_win_r,
                              req.trades_per_day, split_upgrade=req.split_upgrade,
                              sizing=req.sizing, n_sims=n, seed=req.seed)
