"""Pytest isolation for filesystem-backed trading state.

The application intentionally persists journals, bot state, prop accounts, and
history under DATA_DIR.  Unit tests should never share those files: a prior test
that opens positions can otherwise trip daily trade caps in a later risk-gate
case.  Keep each test's DATA_DIR private while preserving persistence within the
same test function.
"""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def isolated_data_dir(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    monkeypatch.setenv("DATA_DIR", str(data_dir))

    # These modules resolve DATA_DIR-derived constants at import time.  Patch the
    # constants too so tests remain isolated even when another test module has
    # already imported the package before this fixture runs.
    from app.trading import store as trading_store
    from app.prop import store as prop_store
    from app.trading.backtest import history as history_store

    monkeypatch.setattr(trading_store, "DATA_DIR", data_dir)
    monkeypatch.setattr(trading_store, "TRADES_CSV", data_dir / "trades.csv")
    monkeypatch.setattr(trading_store, "STATE_JSON", data_dir / "engine_state.json")
    monkeypatch.setattr(trading_store, "POSITIONS_JSON", data_dir / "positions.json")
    monkeypatch.setattr(trading_store, "ACCOUNT_JSON", data_dir / "account.json")

    monkeypatch.setattr(prop_store, "DATA_DIR", data_dir)
    monkeypatch.setattr(prop_store, "ACCOUNTS_JSON", data_dir / "prop_accounts.json")
    monkeypatch.setattr(prop_store, "PAYOUTS_JSON", data_dir / "prop_payouts.json")

    monkeypatch.setattr(history_store, "HISTORY_DIR", data_dir / "history")

    yield
