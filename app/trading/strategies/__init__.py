"""@responsibility 전략 레지스트리 — 이름→전략 클래스 매핑, 신규 전략 등록 지점"""
from .base import TradingContext, TradingStrategy
from .htf_support import HtfSupportStrategy
from .mean_revert import MeanRevertStrategy
from .prop_breakout import PropBreakoutStrategy
from .vbo import VolatilityBreakoutStrategy

STRATEGIES = {
    "prop_breakout": PropBreakoutStrategy,
    "vbo": VolatilityBreakoutStrategy,
    "mean_revert": MeanRevertStrategy,
    "htf_support": HtfSupportStrategy,
}


def make_strategy(name: str, config) -> TradingStrategy:
    if name not in STRATEGIES:
        raise ValueError(f"unknown strategy '{name}' (available: {list(STRATEGIES)})")
    return STRATEGIES[name](config)
