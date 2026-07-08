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
    plan: str = "1step_classic"
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
