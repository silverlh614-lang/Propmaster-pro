"""@responsibility 백테스트 엔진 — 과거 kline을 라이브와 동일한 컨텍스트·포지션 FSM으로 리플레이

Backtest engine. Fetches historical klines (entry + higher timeframe)
and replays them bar-by-bar, rebuilding the exact TradingContext the live bot
would have seen and driving the SAME PositionManager FSM. Risk daily caps are
intentionally OFF (permissive gate) — the goal is raw strategy statistics,
the Phase 2 gate that must pass before any live capital. Settlement is the
FSM's own stop/target/trailing against each bar's high/low (no look-ahead).
"""
from __future__ import annotations

from ..config import SYMBOL_SPECS, TradingConfig, SymbolSpec
from ..indicators import atr
from ..models import Candle
from ..strategies import make_strategy
from ..strategies.base import TradingContext
from ..execution.position import PositionManager

KLINES_REST = "https://fapi.binance.com/fapi/v1/klines"
_BINANCE_IV = {"1": "1m", "3": "3m", "5": "5m", "15": "15m", "30": "30m",
               "60": "1h", "120": "2h", "240": "4h", "360": "6h",
               "720": "12h", "D": "1d", "W": "1w"}


def _interval_min(interval: str) -> int:
    table = {"D": 1440, "W": 10080, "M": 43200}
    return table.get(interval, int(interval))


OKX_REST = "https://www.okx.com/api/v5/market/candles"
_OKX_IV = {"1": "1m", "3": "3m", "5": "5m", "15": "15m", "30": "30m",
           "60": "1H", "120": "2H", "240": "4H", "360": "6H",
           "720": "12H", "D": "1D", "W": "1W"}


def _get_json(url: str, params: dict):
    """One GET (separated so offline tests can stub the network)."""
    import httpx    # lazy: keeps the module importable in offline tests
    with httpx.Client(timeout=15, headers={"User-Agent": "propmaster-pro"}) as c:
        r = c.get(url, params=params)
        r.raise_for_status()
        return r.json()


def fetch_klines(symbol: str, interval: str, limit: int = 1000) -> list[Candle]:
    """Public klines, oldest→newest, forming bar dropped. Binance USDⓈ-M
    first (1000 bars); regions that geo-block fapi fall back to OKX swaps
    (max 300 bars — enough for a direction check, use months= for depth).
    No API key on either path."""
    errors: list[str] = []
    out: list[Candle] = []
    iv = _BINANCE_IV.get(interval)
    if iv is None:
        raise ValueError(f"unsupported interval {interval}")
    try:
        rows = _get_json(KLINES_REST, {"symbol": symbol, "interval": iv,
                                       "limit": min(limit, 1000)})
        if not isinstance(rows, list):
            raise ValueError(str(rows)[:120])
        out = [Candle(ts_ms=int(x[0]), open=float(x[1]), high=float(x[2]),
                      low=float(x[3]), close=float(x[4]), volume=float(x[5]))
               for x in rows]
    except Exception as e:                    # noqa: BLE001 — try next venue
        errors.append(f"binance: {type(e).__name__}: {str(e)[:80]}")
    if not out:
        try:
            base = symbol[:-4] if symbol.endswith("USDT") else symbol
            d = _get_json(OKX_REST, {"instId": f"{base}-USDT-SWAP",
                                     "bar": _OKX_IV[interval],
                                     "limit": min(limit, 300)})
            if str(d.get("code", "0")) != "0":
                raise ValueError(f"code {d.get('code')}: {d.get('msg')}")
            out = [Candle(ts_ms=int(x[0]), open=float(x[1]), high=float(x[2]),
                          low=float(x[3]), close=float(x[4]), volume=float(x[5]))
                   for x in d.get("data", [])]
        except Exception as e:                # noqa: BLE001
            errors.append(f"okx: {type(e).__name__}: {str(e)[:80]}")
    if not out:
        raise RuntimeError("no kline source reachable — " + " | ".join(errors)
                           + " (API 키 문제 아님: 리전 차단이면 months>=1 아카이브 "
                             "모드를 쓰거나 리전을 SG/EU로)")
    out.sort(key=lambda c: c.ts_ms)
    return out[:-1] if out else out          # drop the still-forming bar


class _MemJournal:
    """In-memory journal so a backtest never touches the live CSV."""
    def __init__(self):
        self.rows: list[dict] = []

    def append(self, rec: dict) -> dict:
        self.rows.append(rec)
        return rec


class _PermissiveRisk:
    """Backtest risk shim: no daily caps, no kill switch — raw stats only."""
    def allow_entry(self, *a, **k):
        return True, ""

    def record_ok(self): ...
    def record_error(self, e): ...


def replay(symbol: str, strategy_name: str, cfg: TradingConfig,
           entry_candles: list[Candle] | None = None,
           htf_candles: list[Candle] | None = None,
           months: int = 0) -> dict:
    """Replay one symbol. Candles can be injected (offline tests), fetched
    live (1000-bar REST cap) or, with months > 0, pulled from the Binance
    vision monthly archive (years of history — the Phase 2 default).
    Returns {trades, closes, equity_curve, snapshots}."""
    spec: SymbolSpec = SYMBOL_SPECS[symbol.upper()]
    if entry_candles is None:
        if months > 0:
            from .history import fetch_history
            entry_candles = fetch_history(spec.symbol, cfg.entry_interval, months)
        else:
            entry_candles = fetch_klines(spec.symbol, cfg.entry_interval)
    if htf_candles is None:
        if months > 0:
            from .history import fetch_history
            htf_candles = fetch_history(spec.symbol, cfg.htf_interval, months)
        else:
            htf_candles = fetch_klines(spec.symbol, cfg.htf_interval)
    if not entry_candles or not htf_candles:
        return {"trades": [], "closes": [], "equity_curve": [], "snapshots": 0}

    strategy = make_strategy(strategy_name, cfg)
    pm = PositionManager(spec, cfg, _PermissiveRisk(), _MemJournal(), "backtest",
                         strategy_name)
    journal: _MemJournal = pm.journal   # type: ignore[assignment]
    entry_min = _interval_min(cfg.entry_interval)
    htf_min = _interval_min(cfg.htf_interval)
    warmup = max(cfg.donchian_lookback, cfg.donchian_htf_ema,
                 cfg.atr_period, cfg.ema_period) + 2

    equity_curve: list[float] = []
    snapshots = 0
    # Long histories (months mode) make full-list slices O(n^2) — a sliding
    # window and a monotonic HTF pointer keep the replay linear. WINDOW must
    # cover every strategy lookback (donchian<=55, EMA20 converges < 160).
    WINDOW = max(160, warmup + 2)
    htf_close_ts = [c.ts_ms + htf_min * 60_000 for c in htf_candles]
    j = 0
    for i in range(warmup, len(entry_candles)):
        bar = entry_candles[i]
        closed_entry = entry_candles[max(0, i + 1 - WINDOW):i + 1]
        bar_close_t = bar.ts_ms + entry_min * 60_000
        while j < len(htf_candles) and htf_close_ts[j] <= bar_close_t:
            j += 1
        closed_htf = htf_candles[max(0, j - WINDOW):j]
        if j < warmup:
            continue
        snapshots += 1
        atr_val = atr(closed_entry, cfg.atr_period)

        pm.flatten_if_closed()
        pm.manage(bar, atr_val)
        ctx = TradingContext(symbol=spec.key, htf_candles=closed_htf,
                           entry_candles=closed_entry, equity_usd=pm.equity,
                           now=bar_close_t / 1000,
                           open_position_side=(pm.pos.side.value
                                               if pm.pos and pm.pos.state.value == "OPEN"
                                               else None))
        sig = strategy.evaluate(ctx)
        if sig is not None:
            p = pm.pos
            if p and p.state.value == "OPEN" and sig.side is p.side:
                pm.try_add(sig, bar.close, atr_val or 0.0, ctx.now)
            elif not (p and p.state.value == "OPEN"):
                pm.try_open(sig, bar.close, atr_val or 0.0, ctx.now)
        equity_curve.append(round(pm.equity, 4))

    # force-close any position still open at the end of the data
    if pm.pos and pm.pos.state.value == "OPEN":
        pm._close(entry_candles[-1].close, "backtest end", ctx.now)

    closes = [r for r in journal.rows if r["event"] == "CLOSE"]
    return {"trades": journal.rows, "closes": closes,
            "equity_curve": equity_curve, "snapshots": snapshots,
            "final_equity": round(pm.equity, 4)}


def sweep(symbol: str, strategy_name: str, base_cfg: TradingConfig,
          grid: dict[str, list], months: int = 0,
          entry_candles: list[Candle] | None = None,
          htf_candles: list[Candle] | None = None,
          max_combos: int = 64, on_progress=None) -> list[dict]:
    """Grid-sweep config overrides over ONE candle download. Returns one row
    per combo (overrides + key metrics), sorted by expectancy_r. The whole
    point vs calling /backtest N times: candles are fetched once."""
    import copy
    import itertools

    from .metrics import compute

    keys = list(grid)
    combos = list(itertools.product(*(grid[k] for k in keys)))
    if len(combos) > max_combos:
        raise ValueError(f"{len(combos)} combos > cap {max_combos}")
    spec = SYMBOL_SPECS[symbol.upper()]
    if entry_candles is None or htf_candles is None:
        if months > 0:
            from .history import fetch_history
            entry_candles = fetch_history(spec.symbol, base_cfg.entry_interval, months)
            htf_candles = fetch_history(spec.symbol, base_cfg.htf_interval, months)
        else:
            entry_candles = fetch_klines(spec.symbol, base_cfg.entry_interval)
            htf_candles = fetch_klines(spec.symbol, base_cfg.htf_interval)

    rows: list[dict] = []
    for i, combo in enumerate(combos):
        cfg = copy.copy(base_cfg)
        for k, v in zip(keys, combo):
            cur = getattr(cfg, k)
            setattr(cfg, k, (type(cur)(v) if not isinstance(cur, bool)
                             else bool(v)))
        r = replay(symbol, strategy_name, cfg,
                   entry_candles=entry_candles, htf_candles=htf_candles)
        m = compute(r["closes"], cfg.equity_usd,
                    r.get("final_equity", cfg.equity_usd))
        rows.append({"overrides": dict(zip(keys, combo)),
                     "trades": m["trades"], "win_rate": m["win_rate"],
                     "expectancy_r": m["expectancy_r"],
                     "profit_factor": m["profit_factor"],
                     "return_pct": m["return_pct"],
                     "max_drawdown_usd": m["max_drawdown_usd"]})
        if on_progress is not None:
            on_progress(i + 1, len(combos), rows[-1])
    rows.sort(key=lambda x: (x["expectancy_r"] is None,
                             -(x["expectancy_r"] or 0)))
    return rows
