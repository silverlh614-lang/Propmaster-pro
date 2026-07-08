"""@responsibility 데스크 수익화 원장 — 챌린지 수수료·분할 애드온·페이아웃 스프레드·환불 스트림을 기록·집계

Desk-side monetization ledger. The prop desk earns (simulated) revenue
through four streams, mirroring Breakout Prop's public business model:

  challenge_fee  one-time evaluation fee at every challenge purchase
                 (a failed account never refunds — a retry is a new sale)
  split_addon    the +20% checkout add-on for the permanent 90% split
  payout_spread  the desk's share of every funded payout
                 (amount minus the trader's profit split)
  fee_refund     first-funded-payout refund of the evaluation fee —
                 recorded NEGATIVE (it is a cost against fee revenue)

Every event is appended to the RevenueStore by the desk at the moment the
money moves (purchase / payout) — nothing here recomputes state from
accounts, so the ledger stays a faithful, append-only cash record.
summary() folds the events into per-stream totals plus gross/refund/net.
"""
from __future__ import annotations

import time

from .store import RevenueStore

STREAM_CHALLENGE_FEE = "challenge_fee"
STREAM_SPLIT_ADDON = "split_addon"
STREAM_PAYOUT_SPREAD = "payout_spread"
STREAM_FEE_REFUND = "fee_refund"

STREAMS = (STREAM_CHALLENGE_FEE, STREAM_SPLIT_ADDON,
           STREAM_PAYOUT_SPREAD, STREAM_FEE_REFUND)


class RevenueLedger:
    """Append-only monetization events + aggregation for the API/UI."""

    def __init__(self, store: RevenueStore | None = None):
        self.store = store or RevenueStore()

    def record(self, stream: str, usd: float, account_id: str,
               note: str = "", ts: float | None = None) -> dict:
        if stream not in STREAMS:
            raise ValueError(f"unknown revenue stream '{stream}'")
        rec = {"ts": ts if ts is not None else time.time(),
               "stream": stream, "usd": round(usd, 2),
               "account": account_id}
        if note:
            rec["note"] = note
        self.store.append(rec)
        return rec

    def summary(self) -> dict:
        """Per-stream totals. gross = earning streams, refunds = costs
        (stored negative), net = gross + refunds."""
        totals = {s: 0.0 for s in STREAMS}
        rows = self.store.load()
        for r in rows:
            s = r.get("stream")
            if s in totals:
                totals[s] = round(totals[s] + float(r.get("usd", 0.0)), 2)
        gross = round(totals[STREAM_CHALLENGE_FEE]
                      + totals[STREAM_SPLIT_ADDON]
                      + totals[STREAM_PAYOUT_SPREAD], 2)
        refunds = totals[STREAM_FEE_REFUND]
        return {"streams": totals, "gross_usd": gross,
                "refunds_usd": refunds,
                "net_usd": round(gross + refunds, 2),
                "events": len(rows)}

    def events(self, limit: int = 50) -> list[dict]:
        """Most-recent-first event slice for the API."""
        return self.store.load()[-limit:][::-1]
