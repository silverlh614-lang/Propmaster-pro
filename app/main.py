"""@responsibility FastAPI 엔트리포인트 — 프롭 데스크·트레이딩 API 라우팅과 앱 조립

Propmaster Pro — Breakout-style crypto prop trading platform (simulated).

Prop desk (app/prop/) owns the challenge lifecycle: evaluation -> funded ->
payout, judged by the rule engine on every closed bar. The paper trading
engine (app/trading/) executes. The old BTC cycle-bottom analysis sidecar
was removed — this service is the prop platform, nothing else.
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse

from .prop.api import router as prop_router
from .trading.api import router as trading_router
from .trading.bot import MANAGER as TRADING_MANAGER
from .trading.research import router as research_router

ROOT = Path(__file__).resolve().parent.parent


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Warm the kline feed on boot so the live chart shows candles immediately,
    # even before an operator starts the bot.
    TRADING_MANAGER.start_feeds()
    # 헬스 워치독 기동: 피드가 도는 즉시 정지·무응답을 감시해 폰으로 알린다
    # (매매 시작 전에도 피드는 돌기 때문에 여기서 켠다).
    TRADING_MANAGER.health.start()
    # Auto-resume: if the trading bot was running before a restart/redeploy,
    # start it again with the same mode/strategy (state persists in the
    # DATA_DIR volume).
    bst = TRADING_MANAGER.state_store.load()
    if bst.get("running"):
        await TRADING_MANAGER.start(mode=bst.get("mode", "paper"),
                                    strategy=bst.get("strategy", "prop_breakout"))
    yield
    # Graceful exit WITHOUT persisting running=False, so auto-resume fires
    # on the next boot. An operator pressing "stop" is the only thing that
    # persists an intentional off state.
    await TRADING_MANAGER.health.stop()
    await TRADING_MANAGER.shutdown()
    await TRADING_MANAGER.stop_feeds()


app = FastAPI(
    title="Propmaster Pro — Crypto Prop Trading (Breakout-style)",
    description="Evaluation challenge -> funded account -> on-demand payout, "
                "enforced by an equity-based rule engine over a paper trading "
                "engine. Simulation only — not investment advice.",
    version="2.0.0",
    lifespan=lifespan,
)
app.include_router(trading_router)
app.include_router(prop_router)
app.include_router(research_router)  # 연구 전용 — 외부 시그널 사후검증, 매매 무관


@app.get("/healthz")
def healthz():
    return {"ok": True, "app_mode": "prop"}


# ---------------------------------------------------------------- UI

@app.get("/", response_class=HTMLResponse)
def index():
    """Landing page = Propmaster terminal (trading + prop control tower)."""
    return (ROOT / "static" / "terminal.html").read_text(encoding="utf-8")


@app.get("/terminal", response_class=HTMLResponse)
def terminal_page():
    """Propmaster terminal — leverage-margin trading control tower (paper)."""
    return (ROOT / "static" / "terminal.html").read_text(encoding="utf-8")


@app.get("/manifest.webmanifest")
def manifest():
    return FileResponse(ROOT / "static" / "manifest.webmanifest",
                        media_type="application/manifest+json")


@app.get("/icon.svg")
def icon():
    return FileResponse(ROOT / "static" / "icon.svg", media_type="image/svg+xml")


# PWA / home-screen icons (Android manifest icons + iOS apple-touch-icon).
_PWA_ICONS = ("icon-192.png", "icon-512.png", "icon-maskable-512.png",
              "apple-touch-icon.png")


@app.get("/{name}.png")
def png_icon(name: str):
    """Serve the home-screen icon PNGs from static/ (allow-listed only)."""
    fname = f"{name}.png"
    if fname not in _PWA_ICONS:
        raise HTTPException(404, "not found")
    return FileResponse(ROOT / "static" / fname, media_type="image/png")
