"""@responsibility 프롭 상태 영속화 — 챌린지 계좌·페이아웃 기록 JSON 단일 통로 (DATA_DIR)

JSON persistence for the prop desk: challenge accounts (+ which one is
active) and the payout ledger. Same DATA_DIR volume convention as the
trading stores so everything survives restarts/redeploys together.
"""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = Path(os.getenv("DATA_DIR", ROOT / "data"))
ACCOUNTS_JSON = DATA_DIR / "prop_accounts.json"
PAYOUTS_JSON = DATA_DIR / "prop_payouts.json"

_lock = threading.Lock()


def _read(path: Path, default):
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            pass
    return default


def _write(path: Path, obj) -> None:
    with _lock:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(obj, ensure_ascii=False, indent=2),
                        encoding="utf-8")


class PropStore:
    """Accounts blob: {"active_id": str|None, "accounts": {id: {...}}}."""

    def load(self) -> dict:
        return _read(ACCOUNTS_JSON, {"active_id": None, "accounts": {}})

    def save(self, blob: dict) -> None:
        _write(ACCOUNTS_JSON, blob)


class PayoutStore:
    """Append-only payout records (list of dicts)."""

    def load(self) -> list[dict]:
        return _read(PAYOUTS_JSON, [])

    def append(self, rec: dict) -> None:
        rows = self.load()
        rows.append(rec)
        _write(PAYOUTS_JSON, rows)
