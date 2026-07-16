"""Offline tests for the Phase 3 execution-broker scaffolding — verifies the
live path stays refused (Paper-First invariant #1). Run: python -m tests.test_broker"""
from __future__ import annotations

import os
import tempfile

os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="broker-test-")

from app.trading.config import TradingConfig               # noqa: E402
from app.trading.execution.broker import (                 # noqa: E402
    BreakoutBroker, PaperBroker, live_ready, make_broker)


def test_paper_broker_default():
    """자격증명 없이는 make_broker 가 PaperBroker (실주문 없음)."""
    cfg = TradingConfig()
    b = make_broker(cfg)
    assert isinstance(b, PaperBroker) and b.live is False
    # 페이퍼 브로커는 주문을 시뮬 표시만 하고 실제로 내지 않는다
    r = b.place_order("ETHUSDT", "BUY", 1.0)
    assert r.ok and "paper" in r.reason
    print("ok  make_broker defaults to PaperBroker (no live orders)")


def test_live_gate_never_opens_without_adapter():
    """게이트: live_enabled + 자격증명이 다 있어도 어댑터 미구현이라 live_ready=False
    이고 make_broker 는 여전히 PaperBroker (실주문 경로 없음 — 불변식 #1)."""
    cfg = TradingConfig()
    # 미설정
    ok, why = live_ready(cfg)
    assert not ok and "LIVE_ENABLED" in why
    # 플래그만
    cfg.live_enabled = True
    ok, why = live_ready(cfg)
    assert not ok and "API" in why
    # 플래그 + 자격증명 (그래도 어댑터 미구현이라 막힘)
    cfg.api_key, cfg.api_secret = "k", "s"
    ok, why = live_ready(cfg)
    assert not ok and "미구현" in why
    # 모든 조건에도 실브로커는 반환되지 않는다
    assert isinstance(make_broker(cfg), PaperBroker)
    print("ok  live gate never opens without an implemented adapter")


def test_breakout_stub_refuses_orders():
    """Breakout stub 은 어떤 주문 메서드도 실행하지 않고 NotImplementedError."""
    b = BreakoutBroker("k", "s")
    assert b.live is True and b.name == "breakout"
    for call in (lambda: b.place_order("ETHUSDT", "BUY", 1.0),
                 lambda: b.close_position("ETHUSDT"),
                 lambda: b.fetch_position("ETHUSDT"),
                 lambda: b.fetch_balance()):
        try:
            call()
            assert False, "라이브 주문 메서드가 실행돼선 안 된다"
        except NotImplementedError as e:
            assert "Phase 3" in str(e)
    print("ok  BreakoutBroker stub refuses every order method")


def test_secrets_never_exposed_in_config():
    """as_dict(=/config 응답)는 자격증명 값을 절대 싣지 않는다 (설정 여부만)."""
    cfg = TradingConfig()
    cfg.api_key, cfg.api_secret = "SECRET_KEY", "SECRET_SECRET"
    d = cfg.as_dict()
    assert d["api_key"] == "***set***" and d["api_secret"] == "***set***"
    assert "SECRET_KEY" not in str(d) and "SECRET_SECRET" not in str(d)
    # 미설정이면 빈 문자열
    assert TradingConfig().as_dict()["api_key"] == ""
    print("ok  credentials masked in config output")


def test_manager_holds_paper_broker():
    """TradingManager 는 단일 조립점에서 브로커를 만들고, 기본은 PaperBroker."""
    from app.trading.bot import TradingManager
    mgr = TradingManager(TradingConfig())
    assert isinstance(mgr.broker, PaperBroker) and mgr.broker.live is False
    print("ok  manager assembles a PaperBroker by default")


if __name__ == "__main__":
    test_paper_broker_default()
    test_live_gate_never_opens_without_adapter()
    test_breakout_stub_refuses_orders()
    test_secrets_never_exposed_in_config()
    test_manager_holds_paper_broker()
    print("\nALL BROKER TESTS PASSED")
