"""@responsibility 전략 레지스트리 — 이름→전략 클래스 매핑, 신규 전략 등록 지점"""
from .base import TradingContext, TradingStrategy
from .prop_breakout import PropBreakoutStrategy

STRATEGIES = {
    "prop_breakout": PropBreakoutStrategy,
}


def make_strategy(name: str, config) -> TradingStrategy:
    if name not in STRATEGIES:
        raise ValueError(f"unknown strategy '{name}' (available: {list(STRATEGIES)})")
    return STRATEGIES[name](config)
