"""@responsibility 트레이딩 관제탑 REST API — /api/trading/* 봇 제어·저널·백테스트·설정 조회

REST API for the trading control tower (/api/trading/*)."""
from __future__ import annotations

import csv
import datetime as dt
import io

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel

from .bot import MANAGER
from .config import CONFIG, SYMBOL_SPECS, CANDIDATE_SPECS, spec_for
from .store import FIELDS, SIGNAL_FIELDS
from .strategies import STRATEGIES

router = APIRouter(prefix="/api/trading", tags=["trading"])


class StartRequest(BaseModel):
    mode: str = "paper"
    strategy: str = "prop_breakout"


class ManualRequest(BaseModel):
    action: str            # close
    symbol: str = "BTC"


class SymbolToggleRequest(BaseModel):
    symbol: str
    active: bool = True


@router.get("/status")
def status():
    return MANAGER.status()


@router.post("/start")
async def start(req: StartRequest):
    res = await MANAGER.start(mode=req.mode, strategy=req.strategy)
    if not res.get("ok"):
        raise HTTPException(409, res.get("error", "start failed"))
    return res


@router.post("/stop")
async def stop():
    res = await MANAGER.stop()
    if not res.get("ok"):
        raise HTTPException(409, res.get("error", "stop failed"))
    return res


@router.post("/manual")
def manual(req: ManualRequest):
    res = MANAGER.manual(req.action, symbol=req.symbol)
    if not res.get("ok"):
        raise HTTPException(409, res.get("error", "manual action failed"))
    return res


@router.post("/kill/reset")
def reset_kill():
    MANAGER.risk.reset_kill()
    return {"ok": True}


@router.get("/notify")
def notify_status():
    """텔레그램 알림 설정 상태 (토큰·챗ID 설정 여부, 알림 대상 이벤트). 값은
    노출하지 않고 설정 여부만 반환한다."""
    n = MANAGER.notifier
    return {"enabled": n.enabled,
            "token_configured": bool(n.token),
            "chat_configured": bool(n.chat_id),
            "events": sorted(n.events)}


@router.api_route("/notify/test", methods=["GET", "POST"])
def notify_test():
    """테스트 메시지 1건 발송 — 봇 토큰·챗ID 배선을 트레이드 없이 확인한다.
    GET 도 허용 — 폰 브라우저 주소창에 이 URL 을 열면 바로 발송된다."""
    n = MANAGER.notifier
    if not n.enabled:
        raise HTTPException(
            409, "텔레그램 미설정 — TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID 를 설정하세요")
    n.send_text("🔔 Propmaster Pro 알림 테스트 — 연결 정상")
    return {"ok": True, "sent": True,
            "hint": "텔레그램에 메시지가 오면 배선 정상. 안 오면 토큰·챗ID 재확인"}


@router.post("/symbols")
async def toggle_symbol(req: SymbolToggleRequest):
    """토글 UI: 후보 유니버스의 한 종목을 켜고(위성 봇 가동) 끈다(청산 상태만).
    코어 종목은 거부, 열린 포지션이 있으면 거부. 선택은 재시작에도 유지."""
    res = await MANAGER.toggle_symbol(req.symbol, req.active)
    if not res.get("ok"):
        raise HTTPException(409, res.get("error", "toggle failed"))
    return res


@router.get("/discovery")
def discovery():
    """Auto-discovery snapshot: candidate ranking + rotation state."""
    return MANAGER.discovery.status()


@router.post("/discovery/scan")
async def discovery_scan():
    """Force one scan now. Ranking always returns; rotation applies only
    when TRADING_AUTO_DISCOVERY is on AND the engine is running."""
    try:
        return await MANAGER.discovery.scan_once()
    except Exception as e:  # noqa: BLE001 — surfaced to the operator
        raise HTTPException(502, f"scan failed: {type(e).__name__}: {e}")


@router.get("/candles")
def candles(symbol: str = "BTC", tf: str = "entry", limit: int = 120):
    if symbol.upper() not in SYMBOL_SPECS:
        raise HTTPException(422, f"unknown symbol '{symbol}'")
    if tf not in ("entry", "htf"):
        raise HTTPException(422, "tf must be 'entry' or 'htf'")
    return MANAGER.candles(symbol, tf, min(max(limit, 10), 400))


@router.get("/trades")
def trades(limit: int = 50, symbol: str | None = None):
    sym = None if (not symbol or symbol.lower() in ("all", "")) else symbol.upper()
    if sym and sym not in SYMBOL_SPECS:
        raise HTTPException(422, f"unknown symbol '{symbol}'")
    return {"trades": MANAGER.journal.tail(limit, symbol=sym),
            "aggregate": MANAGER.journal.aggregate(symbol=sym),
            "by_symbol": MANAGER.journal.by_symbol(list(MANAGER.bots)),
            "by_reason": MANAGER.journal.by_reason(symbol=sym),
            "today": MANAGER.risk.today(), "symbol": sym or "ALL"}


@router.get("/trades.csv")
def trades_csv(symbol: str | None = None, limit: int = 100_000):
    sym = None if (not symbol or symbol.lower() in ("all", "")) else symbol.upper()
    rows = MANAGER.journal.tail(limit, symbol=sym)[::-1]
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=FIELDS)
    w.writeheader()
    w.writerows(rows)
    day = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d")
    fname = f"trades_{(symbol or 'all').lower()}_{day}.csv"
    return Response(content="\ufeff" + buf.getvalue(),
                    media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{fname}"'})


@router.get("/signals")
def signals(limit: int = 100, symbol: str | None = None):
    """\uc804\ub7b5 \uc2dc\uadf8\ub110 \uae30\ub85d \uc870\ud68c \u2014 \uccb4\uacb0\uacfc \ubb34\uad00\ud558\uac8c \uc804\ub7b5\uc774 \ub0b8 \ubaa8\ub4e0 \uc9c4\uc785 \uc2e0\ud638(\uae30\uc900\uac00\u00b7
    \ubaa9\ud45c\uac00\u00b7\uc190\uc808\uac00\u00b7\ucc28\ub2e8\uc0ac\uc720\u00b7\uc9c4\uc785\uc5ec\ubd80). \ucd5c\uc2e0\uc21c. stats \ub294 \ucd1d\uacc4/\uc9c4\uc785/\ucc28\ub2e8 \uc694\uc57d."""
    sym = None if (not symbol or symbol.lower() in ("all", "")) else symbol.upper()
    return {"stats": MANAGER.signals.stats(), "rows": MANAGER.signals.tail(limit, symbol=sym),
            "counterfactual": MANAGER.signals.counterfactual(symbol=sym), "excursion": MANAGER.signals.excursion(symbol=sym)}


@router.get("/signals.csv")
def signals_csv(symbol: str | None = None, limit: int = 100_000):
    """\uc2dc\uadf8\ub110 \uae30\ub85d CSV \ub2e4\uc6b4\ub85c\ub4dc (Excel \ud638\ud658, BOM \ud3ec\ud568)."""
    sym = None if (not symbol or symbol.lower() in ("all", "")) else symbol.upper()
    rows = MANAGER.signals.tail(limit, symbol=sym)[::-1]
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=SIGNAL_FIELDS)
    w.writeheader()
    w.writerows(rows)
    day = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d")
    fname = f"signals_{(symbol or 'all').lower()}_{day}.csv"
    return Response(content="\ufeff" + buf.getvalue(),
                    media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{fname}"'})


class BacktestRequest(BaseModel):
    # overrides accepts strings too so interval fields can be swept
    # (htf_interval "60"|"240"|"D") alongside numeric params.
    symbol: str = "BTC"
    strategy: str = "prop_breakout"
    overrides: dict[str, float | str] = {}
    months: int = 0        # 0 = live 1000-bar fetch; N = vision archive months


@router.post("/backtest")
def backtest(req: BacktestRequest):
    import copy

    from .backtest.engine import replay
    from .backtest.metrics import compute

    if spec_for(req.symbol) is None:      # 후보 풀 포함 (백테스트 전용 허용)
        raise HTTPException(422, f"unknown symbol '{req.symbol}'")
    if req.strategy not in STRATEGIES:
        raise HTTPException(422, f"unknown strategy '{req.strategy}'")
    cfg = copy.copy(CONFIG)
    for k, v in req.overrides.items():
        if not hasattr(cfg, k):
            raise HTTPException(422, f"unknown config field '{k}'")
        cur = getattr(cfg, k)
        try:
            if isinstance(cur, bool):   # bool("0") is True — parse explicitly
                val = (v.strip().lower() in ("1", "true", "yes", "on")
                       if isinstance(v, str) else bool(v))
            else:
                val = type(cur)(v)
        except (TypeError, ValueError):
            raise HTTPException(422, f"bad value for '{k}': {v!r}")
        setattr(cfg, k, val)
    if not (0 <= req.months <= 60):
        raise HTTPException(422, "months must be 0..60")
    try:
        r = replay(req.symbol, req.strategy, cfg, months=req.months)
    except Exception as e:
        raise HTTPException(502, f"backtest fetch/replay failed: {e}")
    return {"symbol": req.symbol.upper(), "strategy": req.strategy,
            "metrics": compute(r["closes"], cfg.equity_usd, r.get("final_equity", cfg.equity_usd)),
            "final_equity": r.get("final_equity"),
            "snapshots": r["snapshots"], "trades": len(r["closes"]),
            "equity_curve": r["equity_curve"][-500:]}


class SweepRequest(BaseModel):
    symbol: str = "BTC"
    strategy: str = "prop_breakout"
    months: int = 12
    grid: dict[str, list[float | str]] = {}   # field -> candidate values


@router.post("/backtest/sweep")
def backtest_sweep(req: SweepRequest):
    """Grid A/B over one candle download — e.g. {"donchian_lookback":
    [20, 55], "atr_stop_mult": [1.5, 2.5], "pump_filter_pct": [0, 15]}.
    Rows come back sorted by expectancy_r."""
    import copy

    from .backtest.engine import sweep

    if spec_for(req.symbol) is None:      # 후보 풀 포함 (백테스트 전용 허용)
        raise HTTPException(422, f"unknown symbol '{req.symbol}'")
    if req.strategy not in STRATEGIES:
        raise HTTPException(422, f"unknown strategy '{req.strategy}'")
    if not (0 <= req.months <= 60):
        raise HTTPException(422, "months must be 0..60")
    if not req.grid:
        raise HTTPException(422, "grid must name at least one config field")
    for k in req.grid:
        if not hasattr(CONFIG, k):
            raise HTTPException(422, f"unknown config field '{k}'")
    try:
        rows = sweep(req.symbol, req.strategy, copy.copy(CONFIG), req.grid,
                     months=req.months)
    except ValueError as e:
        raise HTTPException(422, str(e))
    except Exception as e:
        raise HTTPException(502, f"sweep failed: {e}")
    return {"symbol": req.symbol.upper(), "strategy": req.strategy,
            "months": req.months, "combos": len(rows), "results": rows}


def _apply_query_overrides(cfg, params, reserved: tuple) -> dict:
    """모든 여분 쿼리 파라미터를 cfg 필드 오버라이드로 적용한다 (URL-only 백테스트/
    스캔이 공유). reserved 키는 건너뛰고, 나머지는 필드 존재·타입을 검증해 캐스팅한다."""
    applied: dict = {}
    for k, v in params.items():
        if k in reserved:
            continue
        if not hasattr(cfg, k):
            raise HTTPException(422, f"unknown config field '{k}'")
        cur = getattr(cfg, k)
        try:
            val = (v.strip().lower() in ("1", "true", "yes", "on")
                   if isinstance(cur, bool) else type(cur)(v))
        except (TypeError, ValueError):
            raise HTTPException(422, f"bad value for '{k}': {v!r}")
        setattr(cfg, k, val)
        applied[k] = val
    return applied


@router.get("/backtest/quick")
def backtest_quick(request: Request, months: int = 12, symbol: str = "BTC",
                   strategy: str = "prop_breakout"):
    """URL-only backtest (mobile-proof): every extra query param is a config
    override — /backtest/quick?months=3&atr_stop_mult=2.5&entry_interval=60.
    Returns metrics only, small enough to read on screen."""
    import copy

    from .backtest.engine import replay
    from .backtest.metrics import compute

    if spec_for(symbol) is None:          # 후보 풀 포함 (백테스트 전용 허용)
        raise HTTPException(422, f"unknown symbol '{symbol}'")
    if strategy not in STRATEGIES:
        raise HTTPException(422, f"unknown strategy '{strategy}'")
    if not (0 <= months <= 60):
        raise HTTPException(422, "months must be 0..60")
    cfg = copy.copy(CONFIG)
    applied = _apply_query_overrides(cfg, request.query_params,
                                     ("months", "symbol", "strategy"))
    try:
        r = replay(symbol, strategy, cfg, months=months)
    except Exception as e:
        raise HTTPException(502, f"backtest fetch/replay failed: {e}")
    m = compute(r["closes"], cfg.equity_usd,
                r.get("final_equity", cfg.equity_usd))
    return {"symbol": symbol.upper(), "strategy": strategy, "months": months,
            "overrides": applied, "metrics": m,
            "final_equity": r.get("final_equity"), "snapshots": r["snapshots"]}


PRESET_GRID = {"donchian_lookback": [20, 55], "entry_interval": ["15", "60"],
               "atr_stop_mult": [1.5, 2.5], "pump_filter_pct": [0, 10]}

# 프리셋 스윕은 수 분짜리 작업 — 동기 HTTP 는 엣지 타임아웃에 걸린다.
# 단일 백그라운드 잡 슬롯 + 같은 URL 새로고침 폴링 (모바일 친화).
_SWEEP = {"state": "idle", "key": None, "done": 0, "total": 0,
          "partial": [], "results": None, "error": ""}
_SWEEP_LOCK = None  # lazy threading.Lock


def _sweep_worker(key: str, symbol: str, months: int) -> None:
    import copy

    from .backtest.engine import sweep

    def tick(done, total, _row):
        _SWEEP["done"], _SWEEP["total"] = done, total

    try:
        rows = sweep(symbol, "prop_breakout", copy.copy(CONFIG), PRESET_GRID,
                     months=months, on_progress=tick)
        _SWEEP.update(state="done", results=rows)
    except Exception as e:                        # noqa: BLE001 — surfaced via poll
        _SWEEP.update(state="error", error=f"{type(e).__name__}: {e}"[:300])


@router.get("/backtest/sweep/preset")
def backtest_sweep_preset(months: int = 12, symbol: str = "BTC",
                          refresh: int = 0):
    """Mobile-friendly calibration: open this ONE URL — the standard
    16-combo grid starts in the background; REFRESH the same URL to watch
    progress (done/total) until state=done delivers the sorted table.
    ?refresh=1 discards a finished result and reruns."""
    import threading
    global _SWEEP_LOCK
    if _SWEEP_LOCK is None:
        _SWEEP_LOCK = threading.Lock()

    if spec_for(symbol) is None:          # 후보 풀 포함 (백테스트 전용 허용)
        raise HTTPException(422, f"unknown symbol '{symbol}'")
    if not (0 <= months <= 60):
        raise HTTPException(422, "months must be 0..60")
    key = f"{symbol.upper()}:{months}"
    with _SWEEP_LOCK:
        if _SWEEP["state"] == "running":
            return {"state": "running", "key": _SWEEP["key"],
                    "progress": f"{_SWEEP['done']}/{_SWEEP['total'] or '?'}",
                    "hint": "이 URL을 새로고침하면 진행률이 갱신됩니다"}
        if (_SWEEP["state"] == "done" and _SWEEP["key"] == key
                and not refresh):
            return {"state": "done", "symbol": symbol.upper(),
                    "strategy": "prop_breakout", "months": months,
                    "grid": PRESET_GRID, "combos": len(_SWEEP["results"]),
                    "results": _SWEEP["results"]}
        if _SWEEP["state"] == "error" and _SWEEP["key"] == key and not refresh:
            return {"state": "error", "error": _SWEEP["error"],
                    "hint": "?refresh=1 로 재시도"}
        _SWEEP.update(state="running", key=key, done=0, total=0,
                      partial=[], results=None, error="")
        threading.Thread(target=_sweep_worker, args=(key, symbol, months),
                         daemon=True, name="sweep-preset").start()
    return {"state": "started", "key": key,
            "grid": PRESET_GRID, "combos": 16,
            "hint": "계산 시작 — 이 URL을 30초~1분 간격으로 새로고침하세요. "
                    "state=done 이 되면 결과가 이 자리에 표시됩니다"}


# ── 유니버스 스캔 (전 심볼 × 두 전략 백테스트 랭킹) — 프리셋 스윕과 같은
# 백그라운드 잡 + 폴링 패턴. "다른 종목들은 어떤가"를 한 URL로 답한다.
_SCAN = {"state": "idle", "key": None, "done": 0, "total": 0,
         "results": None, "error": ""}
_SCAN_LOCK = None
SCAN_STRATEGIES = ["prop_breakout", "vbo"]


def _scan_worker(key: str, symbols: list, months: int, overrides: dict,
                 strategies: list) -> None:
    import copy

    from .backtest.engine import scan_universe

    def tick(done, total, rows):
        _SCAN["done"], _SCAN["total"] = done, total
        if rows is not None:
            _SCAN["results"] = list(rows)     # 부분 결과도 폴링에 노출

    try:
        cfg = copy.copy(CONFIG)
        for k, v in overrides.items():
            setattr(cfg, k, v)
        rows = scan_universe(symbols, strategies, cfg,
                             months=months, on_progress=tick)
        _SCAN.update(state="done", results=rows)
    except Exception as e:                        # noqa: BLE001 — surfaced via poll
        _SCAN.update(state="error", error=f"{type(e).__name__}: {e}"[:300])


@router.get("/backtest/scan")
def backtest_scan(request: Request, months: int = 12, symbols: str = "",
                  refresh: int = 0, strat: str = ""):
    """전 유니버스(또는 symbols=BNB,ADA,…)를 prop_breakout·vbo(기본) 전략으로
    백테스트해 게이트 통과·expectancy_r 순으로 랭킹한다. 프리셋 스윕처럼 이 URL
    하나를 새로고침하며 진행률을 보고, state=done 이면 표가 나온다. 심볼당 캔들을
    한 번만 받으므로 느리지만(첫 실행), 아카이브는 디스크 캐시된다. 여분 쿼리
    파라미터는 config 오버라이드 — ?trail_atr_mult=3&partial_tp_frac=0.33 처럼
    청산 관리를 A/B 한다. ?strat=mean_revert 로 전략을 바꿔 브레이크아웃 탈락
    레인지 종목을 평균회귀로 재검증할 수 있다."""
    import copy
    import threading
    global _SCAN_LOCK
    if _SCAN_LOCK is None:
        _SCAN_LOCK = threading.Lock()
    if not (0 <= months <= 60):
        raise HTTPException(422, "months must be 0..60")
    if symbols.strip():
        syms = [s.strip().upper() for s in symbols.split(",") if s.strip()]
        bad = [s for s in syms if spec_for(s) is None]   # 후보 풀 포함
        if bad:
            raise HTTPException(422, f"unknown symbols: {', '.join(bad)}")
    else:
        syms = list(SYMBOL_SPECS)
    if strat.strip():
        strategies = [s.strip() for s in strat.split(",") if s.strip()]
        bad_s = [s for s in strategies if s not in STRATEGIES]
        if bad_s:
            raise HTTPException(422, f"unknown strategy: {', '.join(bad_s)}")
    else:
        strategies = SCAN_STRATEGIES
    overrides = _apply_query_overrides(
        copy.copy(CONFIG), request.query_params,
        ("months", "symbols", "refresh", "strat"))
    ov_key = ",".join(f"{k}={overrides[k]}" for k in sorted(overrides))
    key = f"scan:{months}:{','.join(syms)}:{','.join(strategies)}:{ov_key}"
    with _SCAN_LOCK:
        if _SCAN["state"] == "running":
            return {"state": "running", "key": _SCAN["key"],
                    "progress": f"{_SCAN['done']}/{_SCAN['total'] or '?'} 심볼",
                    "partial": _SCAN["results"] or [],
                    "hint": "이 URL을 새로고침하면 진행률·부분결과가 갱신됩니다"}
        if _SCAN["state"] == "done" and _SCAN["key"] == key and not refresh:
            return {"state": "done", "months": months, "overrides": overrides,
                    "strategies": strategies, "symbols": syms,
                    "rows": len(_SCAN["results"]), "results": _SCAN["results"]}
        if _SCAN["state"] == "error" and _SCAN["key"] == key and not refresh:
            return {"state": "error", "error": _SCAN["error"],
                    "hint": "?refresh=1 로 재시도"}
        _SCAN.update(state="running", key=key, done=0, total=len(syms),
                     results=None, error="")
        threading.Thread(target=_scan_worker,
                         args=(key, syms, months, overrides, strategies),
                         daemon=True, name="scan-universe").start()
    return {"state": "started", "key": key, "symbols": syms,
            "strategies": strategies, "overrides": overrides,
            "hint": "계산 시작 — 이 URL을 30초~1분 간격으로 새로고침하세요. "
                    "심볼당 캔들 다운로드라 첫 실행은 수 분 걸립니다"}


@router.get("/live/status")
def live_status():
    """Phase 3 라이브 게이트 상태 (배관). 현재 브로커·라이브 준비 여부·미충족 사유를
    보고한다. 어댑터 미구현이라 ready 는 항상 False (페이퍼-퍼스트). 실주문 경로 없음."""
    from .execution.broker import live_ready
    ready, why = live_ready(CONFIG)
    return {"broker": MANAGER.broker.name, "live": MANAGER.broker.live,
            "live_ready": ready, "reason": why,
            "checklist": {
                "backtest_passed": "docs/phase2_results.md (운영자 확인)",
                "TRADING_LIVE_ENABLED": bool(CONFIG.live_enabled),
                "credentials": bool(CONFIG.api_key and CONFIG.api_secret),
                "adapter_implemented": False},
            "note": "Phase 3 미적용 — 모든 집행은 페이퍼(PositionManager) 시뮬레이션"}


@router.get("/config")
def config():
    return {"config": CONFIG.as_dict(),
            "symbols": list(MANAGER.bots),          # 현재 가동 중(코어+수동+위성)
            "universe": list(SYMBOL_SPECS),         # 거래 가능(토글) 유니버스
            "scan_candidates": list(CANDIDATE_SPECS),  # 백테스트 스캔 전용 후보
            "core": MANAGER.core,                   # 항상 ON (토글 불가)
            "strategies": list(STRATEGIES),
            "phase": "1 (paper only)"}
