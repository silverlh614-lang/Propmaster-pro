"""@responsibility Phase 3 실행 브로커 배관 — 어댑터 인터페이스·페이퍼/Breakout stub·라이브 게이트 (실주문 경로 미작성)

Execution-broker seam for Phase 3. Declares the contract a live venue
adapter must satisfy and provides the paper marker plus a Breakout STUB
whose order methods raise NotImplementedError. NO real order path is
written here — Paper-First invariant #1. make_broker() enforces the Phase 3
gate: a functional live broker is never returned until backtest-passed AND
TRADING_LIVE_ENABLED=1 AND credentials are present AND the adapter is
implemented; until all hold it returns the PaperBroker. This module is the
seam where Phase 3 plugs in — laying the pipes, not turning on the water.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class OrderResult:
    """One order/close outcome — the venue-neutral shape a live adapter and
    the paper marker both return."""
    ok: bool
    order_id: str = ""
    filled_qty: float = 0.0
    avg_price: float = 0.0
    reason: str = ""


@runtime_checkable
class Broker(Protocol):
    """Execution backend contract. Paper simulates via the PositionManager
    FSM; a live adapter routes to the real venue. The minimal surface a
    Phase 3 adapter must implement — kept small on purpose."""
    name: str
    live: bool

    def place_order(self, symbol: str, side: str, qty: float,
                    reduce_only: bool = False) -> OrderResult: ...

    def close_position(self, symbol: str) -> OrderResult: ...

    def fetch_position(self, symbol: str) -> dict | None: ...

    def fetch_balance(self) -> float | None: ...


class PaperBroker:
    """Paper marker. Real fills are simulated by the PositionManager FSM, so
    this broker never places an order — it only signals 'not live'."""
    name = "paper"
    live = False

    def place_order(self, symbol: str, side: str, qty: float,
                    reduce_only: bool = False) -> OrderResult:
        return OrderResult(ok=True, reason="paper: simulated by PositionManager")

    def close_position(self, symbol: str) -> OrderResult:
        return OrderResult(ok=True, reason="paper: simulated by PositionManager")

    def fetch_position(self, symbol: str) -> dict | None:
        return None

    def fetch_balance(self) -> float | None:
        return None


class BreakoutBroker:
    """Breakout (Kraken-backed) live adapter — STUB. Phase 3 fills in the
    REST/signing and real order routing here. Right now every order method
    refuses immediately: no live order path exists yet (invariant #1).
    Credentials arrive only via env (TRADING_API_KEY/SECRET), never in code."""
    name = "breakout"
    live = True

    def __init__(self, api_key: str, api_secret: str):
        self._key = api_key
        self._secret = api_secret

    def _refuse(self):
        raise NotImplementedError(
            "Breakout 라이브 어댑터 미구현 — Phase 3. Breakout API 문서·자격증명 "
            "확보 후 place_order/close_position/fetch_* 를 구현하고, 데모/테스트넷 "
            "검증을 거친 뒤에만 활성화한다. 그 전에는 실주문 경로가 존재하지 않는다.")

    def place_order(self, symbol: str, side: str, qty: float,
                    reduce_only: bool = False) -> OrderResult:
        self._refuse()

    def close_position(self, symbol: str) -> OrderResult:
        self._refuse()

    def fetch_position(self, symbol: str) -> dict | None:
        self._refuse()

    def fetch_balance(self) -> float | None:
        self._refuse()


def live_ready(cfg) -> tuple[bool, str]:
    """Phase 3 라이브 게이트. 모든 조건 충족 시에만 True; 하나라도 빠지면 이유와
    함께 False. 백테스트 통과는 운영자가 docs/phase2_results.md 로 판단하고,
    코드는 (1) 명시 플래그 (2) 자격증명 (3) 어댑터 구현 여부를 강제한다.
    어댑터가 미구현인 현재 단계에서는 절대 True 를 반환하지 않는다."""
    if not getattr(cfg, "live_enabled", False):
        return False, "TRADING_LIVE_ENABLED 미설정 — 기본 페이퍼 (Phase 3 게이트)"
    if not (getattr(cfg, "api_key", "") and getattr(cfg, "api_secret", "")):
        return False, "TRADING_API_KEY/TRADING_API_SECRET 없음"
    # 어댑터 미구현 — 여기서 항상 막힌다. Phase 3 구현 완료 시 이 줄을 제거한다.
    return False, "Breakout 어댑터 미구현 (Phase 3) — 실주문 경로 아직 없음"


def make_broker(cfg) -> Broker:
    """브로커 선택점 (단일 조립). 라이브 게이트가 완전히 열리기 전에는 항상
    PaperBroker 를 반환한다. 현재는 live_ready 가 절대 True 가 아니므로 실브로커는
    결코 반환되지 않는다 — 페이퍼-퍼스트 강제. Phase 3 에서 어댑터를 구현하고
    live_ready 를 열면 이 팩토리가 자동으로 BreakoutBroker 를 배선한다."""
    ok, _why = live_ready(cfg)
    if ok:                                    # 현재 도달 불가 (어댑터 미구현)
        return BreakoutBroker(cfg.api_key, cfg.api_secret)
    return PaperBroker()
