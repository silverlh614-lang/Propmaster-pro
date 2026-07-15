"""@responsibility 트레이딩 엔진 설정 SSOT — TRADING_* env 오버라이드 + 심볼별 SymbolSpec

Leverage-margin trading engine configuration. Every field can be
overridden with a TRADING_* environment variable (Railway variables),
e.g. TRADING_RISK_PER_TRADE_PCT=0.5.

Defaults encode prop discipline: leverage <= 5x (symbol class caps),
budget-based risk, 2:1 reward:risk, ATR stops. They are conservative
guesses meant to be calibrated by the Phase 2 backtest gate before any
live trading — do NOT hand-tune them.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, fields


def _env(name: str, default):
    raw = os.getenv(f"TRADING_{name.upper()}")
    if raw is None:
        return default
    if isinstance(default, bool):
        return raw.strip().lower() in ("1", "true", "yes", "on")
    if isinstance(default, int):
        return int(raw)
    if isinstance(default, float):
        return float(raw)
    return raw


@dataclass
class TradingConfig:
    # --- account (paper simulation) ----------------------------------------
    equity_usd: float = 200.0            # 소액: paper starting equity
    quote: str = "USDT"

    # --- timeframes (kline interval codes: minutes as string / D,W) --------
    # Phase 2 게이트 채택값 (2026-07 스윕): 1h 진입 — 15m 대비 수수료 밀도가
    # 1/4이고 12/6/3개월 창 전부 양(+)이었던 유일한 축.
    entry_interval: str = "60"           # 진입 시간봉
    htf_interval: str = "60"             # 추세 필터 시간봉 (검증된 조합 그대로)
    warmup_bars: int = 200               # REST backfill on start

    # --- 차트 오버레이 (표시 전용 — 시그널에 미사용) ------------------------
    ema_period: int = 20                 # 라이브 차트 EMA 라인

    # --- risk management (ATR sizing) ---------------------------------------
    atr_period: int = 14
    # 2026-07 스윕: ATR×2.5 는 4/4 조합 +, ×1.5 는 4/4 조합 - (지배 변수)
    atr_stop_mult: float = 2.5           # hard stop = k * ATR from entry
    risk_per_trade_pct: float = 1.0      # 1회 리스크 상한 캡 (예산 사이징의 ceiling)
    leverage: float = 3.0                # target leverage
    leverage_max: float = 5.0            # HARD cap — never more

    # --- exit: 2:1 R:R + partial + trailing --------------------------------
    rr_target: float = 2.0               # first take-profit at 2R
    partial_tp_frac: float = 0.5         # exit this fraction of size at TP1
    breakeven_after_tp: bool = True      # move stop to entry after partial
    trail_atr_mult: float = 2.0          # trail remainder by k * ATR
    time_stop_bars: int = 0              # 0 = off; else force-exit after N bars

    # --- prop 예산 사이징 -----------------------------------------------------
    # 리스크는 프롭 계좌의 "잔여 예산"에서 나온다: 잔여 일일예산의 25% AND
    # 잔여 최대DD 예산의 10% 중 작은 쪽 (risk_per_trade_pct 는 상한 캡으로만).
    # 손실이 쌓이면 사이즈가 자동으로 줄어 플로어를 지킨다. 프롭 계좌가 없으면
    # (백테스트·스탠드얼론) 고정 비율로 폴백.
    prop_mode: bool = True
    risk_daily_budget_frac: float = 0.25  # per-trade risk <= 잔여 일일예산 * frac
    risk_dd_budget_frac: float = 0.10     # per-trade risk <= 잔여 DD예산 * frac
    daily_stop_after_losses: int = 3      # 당일 N패 도달 시 그날 진입 정지
    daily_open_risk_frac: float = 0.5     # 오픈리스크 합 <= 잔여 일일예산 * frac
    # 연패 쿨다운 (hoc-trade 50만 데이터셋: "2연패 후 쿨다운"이 복수매매를
    # 유의하게 줄인 유일한 검증된 개입). N연패 시 마지막 손실 후 M분 진입 차단.
    cooldown_after_losses: int = 0        # 0=off; 예: 2
    cooldown_minutes: int = 60
    # 00:30 UTC 일일리셋 함정 방어: 리셋 N분 전 마감봉에서 열린 포지션 청산
    # (리셋 순간 미실현 손실을 크게 열어두면 새 일일 플로어를 즉시 위반 — 리포트
    # $2.87 초과 탈락 사례). 0=off (백테스트 게이트로 켤지 판단).
    flatten_before_reset_min: int = 0

    # --- prop_breakout: Donchian 채널 돌파 + HTF 추세 필터 -------------------
    donchian_lookback: int = 55           # 채널 봉 수 — 게이트 채택값 (터틀 스타일)
    donchian_htf_ema: int = 20            # HTF 추세 필터 EMA 기간

    # --- vbo: 래리 윌리엄스 변동성 돌파 (전기 범위×K, 대체 전략) -------------
    vbo_k: float = 0.5                    # 돌파 임계 = 시가 + K×전기 범위
    vbo_range_bars: int = 24              # "전 세션" 범위 산정 봉 수 (1h면 하루)
    # 선택 필터 (기본 OFF — 백테스트 게이트 A/B로만 켠다, hand-tune 금지)
    pump_filter_pct: float = 0.0          # 채널 저점 대비 급등 % 초과 돌파 스킵 (NFI 펌프 필터)
    squeeze_gate: bool = False            # 직전 봉 BB(20,2) ⊂ Keltner(20,1.5ATR) 요구
    breakeven_at_r: float = 0.0           # N R 도달 시 손절→본전 (0=off, 부분익절 전 단계)

    # --- 추세강화 확인 게이트 (박스권 휩쏘 방어 — 전 돌파 전략 공용, 기본 OFF) ---
    # 출처 이론: 거래량=세력(돌파 확인), 장악형 몸통(변동성 군집·range 에너지),
    # 횡보장도 추세의 일종 → 인지되면 관망. 켜는 것도 백테스트 게이트 A/B로만.
    volume_gate_mult: float = 0.0         # 돌파봉 거래량 ≥ mult×SMA(vol) 요구 (0=off)
    volume_ma_period: int = 20            # 거래량 SMA 기간
    engulf_gate: bool = False             # 돌파봉 몸통 > 직전봉 몸통(장악형) 요구
    chop_gate_flips: int = 0              # 최근 window HTF봉 EMA 플립 ≥ N이면 관망 (0=off)
    chop_window: int = 20                 # 플립 계수 구간 (HTF 봉 수)

    # --- 애드업 / pyramiding (손실 뒤 증액과 한 끗 — prop 기본 OFF) ----------
    pyramid_enabled: bool = False
    pyramid_max_adds: int = 2            # never add more than this many units
    pyramid_min_r: float = 1.0           # only add once price is >= this R ahead

    # --- fees (USDT perp taker; makers rebate, we assume taker) -------------
    taker_fee_frac: float = 0.00055      # 0.055% of notional per side

    # --- risk caps (GLOBAL across symbols) ---------------------------------
    max_trades_per_day: int = 20
    daily_loss_cap_pct: float = 6.0      # halt entries at -6% of equity/day
    # 여러 종목 동시 보유 허용 (개수 상한). 진짜 리스크 관문은 아래
    # max_total_open_risk_pct — 동시 포지션 수가 늘어도 전 심볼 오픈리스크 합은
    # 그대로 전역 상한에 묶인다 (프롭 모드는 예산 사이징으로 자동 축소). 우회가
    # 아니라 파라미터 조정 — allow_entry 가 두 캡을 모두 계속 강제한다.
    max_concurrent_positions: int = 5    # was 1; 다종목 동시 진입 허용
    max_total_open_risk_pct: float = 2.0 # sum of open-position risk cap (adds!)
    max_consecutive_errors: int = 5

    # --- 자동 종목 발굴 (auto-discovery — 기본 OFF, 연구 단계) ---------------
    # 코어(TRADING_SYMBOLS, 게이트 검증)는 절대 로테이션하지 않는다. 위성
    # 슬롯만 스캐너가 유동성·변동성·추세효율 랭킹으로 채운다. 열린 포지션이
    # 있는 심볼은 랭킹에서 밀려도 유지(히스테리시스). 상세: docs/auto_discovery.md
    auto_discovery: bool = False          # TRADING_AUTO_DISCOVERY=1 로 켠다
    discovery_top_n: int = 2              # 위성 슬롯 수 (코어와 별개)
    discovery_interval_min: int = 240     # 스캔 주기 (분)
    discovery_min_quote_vol_usdt: float = 5_000_000.0  # 24진입봉 명목 거래대금 플로어
    discovery_er_window: int = 20         # 추세효율(Kaufman ER) HTF 봉 수

    # --- poll / data -------------------------------------------------------
    poll_sec: float = 2.0

    # --- mode (Paper-First) ------------------------------------------------
    live_enabled: bool = False           # Phase 3: TRADING_LIVE_ENABLED=1 + creds

    def __post_init__(self):
        for f in fields(self):
            setattr(self, f.name, _env(f.name, getattr(self, f.name)))
        # invariant: leverage can never exceed the hard cap, whatever env says
        self.leverage = min(self.leverage, self.leverage_max)

    def as_dict(self) -> dict:
        return {f.name: getattr(self, f.name) for f in fields(self)}


CONFIG = TradingConfig()


# --------------------------------------------------------------- symbols

@dataclass(frozen=True)
class SymbolSpec:
    """Per-symbol wiring for USDT linear perpetual markets."""
    key: str                    # display key, e.g. "BTC"
    symbol: str                 # instrument symbol, e.g. "BTCUSDT"
    qty_step: float             # base-asset quantity rounding step
    min_qty: float              # exchange minimum order quantity
    tick_size: float            # price rounding step
    leverage_cap: float = 5.0   # prop rule: majors 5x, alts 2x (Breakout-style)

    def effective_leverage_max(self, global_cap: float) -> float:
        """Binding leverage ceiling for this symbol = the tighter of the
        global hard cap and the symbol class cap."""
        return min(global_cap, self.leverage_cap)


# 메이저(BTC/ETH) 5x, 알트 2x — Breakout 레버리지 클래스. step/tick 은
# Binance USDⓈ-M 근사치 (페이퍼 시뮬 반올림용).
SYMBOL_SPECS: dict[str, SymbolSpec] = {
    "BTC": SymbolSpec("BTC", "BTCUSDT", qty_step=0.001, min_qty=0.001,
                      tick_size=0.1, leverage_cap=5.0),
    "ETH": SymbolSpec("ETH", "ETHUSDT", qty_step=0.01, min_qty=0.01,
                      tick_size=0.01, leverage_cap=5.0),
    "SOL": SymbolSpec("SOL", "SOLUSDT", qty_step=0.1, min_qty=0.1,
                      tick_size=0.001, leverage_cap=2.0),
    "XRP": SymbolSpec("XRP", "XRPUSDT", qty_step=0.1, min_qty=0.1,
                      tick_size=0.0001, leverage_cap=2.0),
    "BNB": SymbolSpec("BNB", "BNBUSDT", qty_step=0.01, min_qty=0.01,
                      tick_size=0.01, leverage_cap=2.0),
    "DOGE": SymbolSpec("DOGE", "DOGEUSDT", qty_step=1.0, min_qty=1.0,
                       tick_size=0.00001, leverage_cap=2.0),
    "ADA": SymbolSpec("ADA", "ADAUSDT", qty_step=1.0, min_qty=1.0,
                      tick_size=0.0001, leverage_cap=2.0),
    "AVAX": SymbolSpec("AVAX", "AVAXUSDT", qty_step=1.0, min_qty=1.0,
                       tick_size=0.001, leverage_cap=2.0),
    "LINK": SymbolSpec("LINK", "LINKUSDT", qty_step=0.01, min_qty=0.01,
                       tick_size=0.001, leverage_cap=2.0),
    "LTC": SymbolSpec("LTC", "LTCUSDT", qty_step=0.001, min_qty=0.001,
                      tick_size=0.01, leverage_cap=2.0),
    # --- 확장 유니버스 (auto-discovery 후보 풀) — 전부 알트 2x 클래스.
    # Binance USDⓈ-M / OKX 스왑 양쪽에 상장된 심볼만 (폴백 유지 조건).
    "DOT": SymbolSpec("DOT", "DOTUSDT", qty_step=0.1, min_qty=0.1,
                      tick_size=0.001, leverage_cap=2.0),
    "ATOM": SymbolSpec("ATOM", "ATOMUSDT", qty_step=0.01, min_qty=0.01,
                       tick_size=0.001, leverage_cap=2.0),
    "NEAR": SymbolSpec("NEAR", "NEARUSDT", qty_step=1.0, min_qty=1.0,
                       tick_size=0.001, leverage_cap=2.0),
    "APT": SymbolSpec("APT", "APTUSDT", qty_step=0.1, min_qty=0.1,
                      tick_size=0.001, leverage_cap=2.0),
    "ARB": SymbolSpec("ARB", "ARBUSDT", qty_step=0.1, min_qty=0.1,
                      tick_size=0.0001, leverage_cap=2.0),
    "OP": SymbolSpec("OP", "OPUSDT", qty_step=0.1, min_qty=0.1,
                     tick_size=0.0001, leverage_cap=2.0),
    "SUI": SymbolSpec("SUI", "SUIUSDT", qty_step=0.1, min_qty=0.1,
                      tick_size=0.0001, leverage_cap=2.0),
    "UNI": SymbolSpec("UNI", "UNIUSDT", qty_step=1.0, min_qty=1.0,
                      tick_size=0.001, leverage_cap=2.0),
    "INJ": SymbolSpec("INJ", "INJUSDT", qty_step=0.1, min_qty=0.1,
                      tick_size=0.001, leverage_cap=2.0),
    "TON": SymbolSpec("TON", "TONUSDT", qty_step=0.1, min_qty=0.1,
                      tick_size=0.0001, leverage_cap=2.0),
}

# 2026-07 게이트: 같은 프로필이 ETH 에서 무보정 아웃오브샘플로 통과
# (12mo 35건 PF 2.10 +19.7% MDD -$15) — 두 심볼 기본 가동. 동시 포지션은
# 여전히 전역 1개(max_concurrent_positions)라 리스크는 그대로, 기회만 늘어난다.
# 되돌리려면 TRADING_SYMBOLS=BTC.
DEFAULT_SYMBOLS = "BTC,ETH"


def enabled_symbols() -> list[SymbolSpec]:
    raw = os.getenv("TRADING_SYMBOLS", DEFAULT_SYMBOLS)
    out = []
    for k in raw.split(","):
        k = k.strip().upper()
        if k in SYMBOL_SPECS:
            out.append(SYMBOL_SPECS[k])
    return out or [SYMBOL_SPECS["BTC"]]


def strategy_for(symbol_key: str, default: str) -> str:
    """Per-symbol strategy resolution. The backtest gate found different
    optima per asset (majors -> Donchian, choppy alts -> volatility
    breakout), so TRADING_SYMBOL_STRATEGY lets each symbol run its own —
    e.g. "BTC:prop_breakout,SOL:vbo". Symbols absent from the map fall back
    to `default` (the strategy passed to /start). Empty env = uniform."""
    raw = os.getenv("TRADING_SYMBOL_STRATEGY", "").strip()
    if not raw:
        return default
    for pair in raw.split(","):
        if ":" in pair:
            k, v = pair.split(":", 1)
            if k.strip().upper() == symbol_key.upper():
                return v.strip()
    return default
