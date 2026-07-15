"""@responsibility 자동 종목 발굴 — kline 단일 통로 스캔으로 위성 심볼 랭킹·로테이션, 코어 불변·기본 OFF

Auto-discovery scanner. Periodically ranks the candidate universe
(SYMBOL_SPECS minus the gate-validated core) on three structural metrics
computed ONLY from klines fetched through the KlineCollector single price
path (invariant #4):

  - quote_vol_usdt : notional turnover of the last VOL_BARS entry bars
                     (liquidity floor — illiquid symbols never trade)
  - atr_pct        : ATR(14) / last close, in % (breakouts need movement)
  - efficiency     : Kaufman efficiency ratio over HTF closes (trend vs
                     chop — the repo's chop research says breakouts bleed
                     in range regimes)

score = efficiency * atr_pct (monotonic "trending AND moving" preference).
The score is a structured opinion for UNIVERSE SELECTION only — it is not
a trade signal, and its weights are subject to the Phase 2 backtest gate,
not hand-tuning.

Rotation is core-satellite: the core (TRADING_SYMBOLS) is never rotated;
only discovery_top_n satellite slots follow the ranking. A satellite with
an open position is always kept (hysteresis), and a provider failure never
rotates anything out (provider 장애 ≠ 시장 신호).
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass

import httpx

from .collectors.kline import KlineCollector
from .config import SYMBOL_SPECS, SymbolSpec, enabled_symbols
from .indicators import atr

DISCOVERY_HTF = "240"   # trend-efficiency timeframe (4h)
SCAN_BARS = 60          # bars fetched per interval per candidate
VOL_BARS = 24           # entry bars summed for the turnover estimate
ATR_PERIOD = 14


# ------------------------------------------------------------ pure metrics

@dataclass(frozen=True)
class SymbolMetrics:
    key: str
    quote_vol_usdt: float
    atr_pct: float
    efficiency: float

    @property
    def score(self) -> float:
        return round(self.efficiency * self.atr_pct, 6)

    def as_dict(self) -> dict:
        return {"key": self.key, "score": self.score,
                "quote_vol_usdt": round(self.quote_vol_usdt, 0),
                "atr_pct": round(self.atr_pct, 4),
                "efficiency": round(self.efficiency, 4)}


def efficiency_ratio(closes: list[float], window: int) -> float | None:
    """Kaufman ER over the last `window` closes: net move / path length.
    ~1 = clean trend, ~0 = chop. None if too few closes."""
    if window < 2 or len(closes) < window + 1:
        return None
    tail = closes[-(window + 1):]
    path = sum(abs(b - a) for a, b in zip(tail, tail[1:]))
    if path <= 0:
        return 0.0
    return abs(tail[-1] - tail[0]) / path


def compute_metrics(key: str, entry_candles: list, htf_candles: list,
                    er_window: int) -> SymbolMetrics | None:
    """Metrics from CLOSED candles only; None when history is too thin to
    judge (a thin symbol is skipped, never guessed)."""
    if len(entry_candles) < max(VOL_BARS, ATR_PERIOD + 1):
        return None
    a = atr(entry_candles, ATR_PERIOD)
    er = efficiency_ratio([c.close for c in htf_candles], er_window)
    last = entry_candles[-1].close
    if a is None or er is None or last <= 0:
        return None
    qv = sum(c.close * c.volume for c in entry_candles[-VOL_BARS:])
    return SymbolMetrics(key=key, quote_vol_usdt=qv,
                         atr_pct=a / last * 100.0, efficiency=er)


def rank(metrics: list[SymbolMetrics],
         min_quote_vol: float) -> list[SymbolMetrics]:
    """Liquidity floor first, then best score first."""
    liquid = [m for m in metrics if m.quote_vol_usdt >= min_quote_vol]
    return sorted(liquid, key=lambda m: m.score, reverse=True)


def select_satellites(ranked: list[str], top_n: int, held: list[str],
                      active: list[str], scanned: list[str]) -> list[str]:
    """Next satellite set. Priority: (1) held positions always stay, even
    beyond top_n; (2) an active satellite the scan could NOT see is kept
    (provider gap is not a rotation signal); (3) remaining slots fill from
    the ranking."""
    keep = list(dict.fromkeys(held))
    for k in active:
        if k not in keep and k not in scanned:
            keep.append(k)
    for k in ranked:
        if len(keep) >= top_n:
            break
        if k not in keep:
            keep.append(k)
    return keep


# ----------------------------------------------------------- orchestration

class AutoDiscovery:
    """Owned by TradingManager. Scans on a schedule while the engine runs;
    rotation only ever touches satellite bots — entries of a newly added
    bot still pass RiskManager.allow_entry like any other."""

    def __init__(self, manager):
        self.mgr = manager
        self.cfg = manager.cfg
        self.core = [s.key for s in enabled_symbols()]
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()
        self._lock = asyncio.Lock()
        self.last_scan_ts: float | None = None
        self.last_ranking: list[dict] = []
        self.note = "off" if not self.cfg.auto_discovery else "idle"

    def candidates(self) -> list[SymbolSpec]:
        return [s for k, s in SYMBOL_SPECS.items() if k not in self.core]

    # ------------------------------------------------------------- scan

    async def _fetch_metrics(self, client: httpx.AsyncClient,
                             spec: SymbolSpec) -> SymbolMetrics | None:
        """One candidate through the single price path (KlineCollector)."""
        col = KlineCollector(spec.symbol, self.cfg.entry_interval,
                             DISCOVERY_HTF, limit=SCAN_BARS)
        await col.poll_once(client)
        return compute_metrics(spec.key, col.entry_closed(),
                               col.closed_candles(DISCOVERY_HTF),
                               self.cfg.discovery_er_window)

    async def scan_once(self) -> dict:
        """Rank the whole candidate pool; apply rotation only when the
        feature is on and the engine is running."""
        async with self._lock:
            scanned: list[str] = []
            metrics: list[SymbolMetrics] = []
            ua = "Mozilla/5.0 (compatible; propmaster-pro/1.0)"
            async with httpx.AsyncClient(timeout=12,
                                         headers={"User-Agent": ua}) as client:
                for spec in self.candidates():
                    try:
                        m = await self._fetch_metrics(client, spec)
                    except asyncio.CancelledError:
                        raise
                    except Exception:   # noqa: BLE001 — 후보 스킵, 신호 아님
                        m = None
                        scanned_ok = False
                    else:
                        scanned_ok = True
                    if scanned_ok:
                        scanned.append(spec.key)
                    if m is not None:
                        metrics.append(m)
            ranked = rank(metrics, self.cfg.discovery_min_quote_vol_usdt)
            self.last_scan_ts = time.time()
            self.last_ranking = [m.as_dict() for m in ranked]
            applied = None
            if self.cfg.auto_discovery and self.mgr.running and scanned:
                applied = await self._apply([m.key for m in ranked], scanned)
            self.note = (f"scanned {len(scanned)}/{len(self.candidates())}, "
                         f"{len(ranked)} ranked"
                         + (f", active {applied['active']}" if applied else ""))
            return {"ok": True, "scanned": len(scanned),
                    "ranking": self.last_ranking, "applied": applied}

    async def _apply(self, ranked_keys: list[str],
                     scanned: list[str]) -> dict:
        """Rotate satellite bots toward the ranking. Core bots and any bot
        holding an open position are untouchable."""
        mgr = self.mgr
        active = [k for k in mgr.bots if k not in self.core]
        held = [k for k in active
                if mgr.bots[k].pm and mgr.bots[k].pm.pos
                and mgr.bots[k].pm.pos.state.value == "OPEN"]
        target = select_satellites(ranked_keys, self.cfg.discovery_top_n,
                                   held, active, scanned)
        dropped, added = [], []
        for k in active:
            if k in target:
                continue
            bot = mgr.bots.pop(k)
            await bot.shutdown()
            await bot.stop_feed()
            mgr.risk.unregister_book(k)
            dropped.append(k)
        for k in target:
            if k in mgr.bots or k not in SYMBOL_SPECS:
                continue
            bot = mgr._make_bot(SYMBOL_SPECS[k])
            mgr.bots[k] = bot
            await bot.start(mgr.mode, mgr.strategy_name)
            added.append(k)
        return {"active": self.core + target, "added": added,
                "dropped": dropped, "held": held}

    # ------------------------------------------------------------- loop

    def start(self) -> None:
        """Idempotent; no-op unless TRADING_AUTO_DISCOVERY is on."""
        if not self.cfg.auto_discovery:
            return
        if self._task is not None and not self._task.done():
            return
        self._stop = asyncio.Event()
        self._task = asyncio.create_task(self._loop(), name="discovery")

    async def stop(self) -> None:
        if self._task is None:
            return
        self._stop.set()
        self._task.cancel()
        await asyncio.gather(self._task, return_exceptions=True)
        self._task = None

    async def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                await self.scan_once()
            except asyncio.CancelledError:
                break
            except Exception as e:  # noqa: BLE001 — 스캔 실패는 현상 유지
                self.note = f"scan error: {type(e).__name__}: {e}"[:200]
            try:
                await asyncio.wait_for(
                    self._stop.wait(),
                    timeout=self.cfg.discovery_interval_min * 60)
            except asyncio.TimeoutError:
                pass

    def status(self) -> dict:
        return {"enabled": self.cfg.auto_discovery,
                "running": self._task is not None and not self._task.done(),
                "core": self.core,
                "top_n": self.cfg.discovery_top_n,
                "interval_min": self.cfg.discovery_interval_min,
                "candidates": [s.key for s in self.candidates()],
                "last_scan_ts": self.last_scan_ts,
                "ranking": self.last_ranking,
                "note": self.note}
