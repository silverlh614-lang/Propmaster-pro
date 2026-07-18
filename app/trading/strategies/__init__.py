"""@responsibility 전략 레지스트리 — 이름→전략 클래스 매핑, 신규 전략 등록 지점"""
from .base import TradingContext, TradingStrategy
from .mean_revert import MeanRevertStrategy
from .prop_breakout import PropBreakoutStrategy
from .vbo import VolatilityBreakoutStrategy

STRATEGIES = {
    "prop_breakout": PropBreakoutStrategy,
    "vbo": VolatilityBreakoutStrategy,
    "mean_revert": MeanRevertStrategy,
}


def make_strategy(name: str, config) -> TradingStrategy:
    if name not in STRATEGIES:
        raise ValueError(f"unknown strategy '{name}' (available: {list(STRATEGIES)})")
    return STRATEGIES[name](config)
