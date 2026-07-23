"""@responsibility 실시간 청산 이벤트 피드 — Binance forceOrder 스트림 표시 전용 (매매·리스크와 완전 분리)

Display-only liquidation event feed. Subscribes to Binance USDⓈ-M futures'
public `!forceOrder@arr` websocket (the ONLY public source of liquidation
events — no REST equivalent exists) and keeps a rolling buffer of recent
forced liquidations for the dashboard.

STRICTLY CONTEXT, NEVER SIGNAL: nothing here is consulted by strategies,
the risk gate, sizing or the prop rules (invariant — provider data must not
become a trade trigger; 청산맵 검토 결과 백테스트 불가로 매매 반영은 기각,
표시 전용만 채택). The feed starts lazily on the first API call, reconnects
with backoff, and swallows every error — a dead stream shows "disconnected"
in the UI and affects nothing else.

Message shape (Binance): {"o":{"s":"BTCUSDT","S":"SELL","q":"0.014",
"ap":"9910.1","T":1568014460893,...}} — side SELL = a LONG got liquidated
(forced market sell), BUY = a SHORT got liquidated.
"""
from __future__ import annotations

import asyncio
import json
import os
import threading
import time
from collections import deque

from fastapi import APIRouter

WS_URL = os.getenv("LIQ_FEED_URL",
                   "wss://fstream.binance.com/ws/!forceOrder@arr")
_MIN_NOTIONAL = float(os.getenv("LIQ_MIN_NOTIONAL", "5000"))   # 표시 최소 $ (먼지 제거)
_BUF_MAX = 120
_HOUR = 3600.0


def _parse(raw: str) -> dict | None:
    """forceOrder 원문 한 건 → 표시용 이벤트. 형식이 다르거나 최소 노셔널
    미만이면 None (파싱 실패가 피드를 죽이지 않도록 예외는 삼킨다)."""
    try:
        o = json.loads(raw).get("o", {})
        price = float(o["ap"] or o["p"])
        qty = float(o["q"])
        notional = price * qty
        if notional < _MIN_NOTIONAL:
            return None
        return {
            "ts": float(o["T"]) / 1000.0,
            "symbol": str(o["s"]),
            # SELL 체결 = 롱 포지션 강제청산, BUY 체결 = 숏 강제청산
            "side": "LONG_LIQ" if o["S"] == "SELL" else "SHORT_LIQ",
            "price": price,
            "notional_usd": round(notional, 2),
        }
    except Exception:                    # noqa: BLE001 — 표시 전용, 절대 안 죽는다
        return None


class LiquidationFeed:
    """백그라운드 데몬 스레드에서 웹소켓을 유지하는 롤링 버퍼. 매매 경로와
    공유하는 상태가 없다 — status() 만 읽힌다."""

    def __init__(self):
        self._events: deque[dict] = deque(maxlen=_BUF_MAX)
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self.connected = False
        self.last_error = ""

    # ------------------------------------------------------------- ingest

    def _ingest(self, evt: dict) -> None:
        with self._lock:
            self._events.append(evt)

    def _hour_totals(self, now: float | None = None) -> dict:
        cut = (now if now is not None else time.time()) - _HOUR
        with self._lock:
            recent = [e for e in self._events if e["ts"] >= cut]
        return {
            "count": len(recent),
            "long_usd": round(sum(e["notional_usd"] for e in recent
                                  if e["side"] == "LONG_LIQ"), 2),
            "short_usd": round(sum(e["notional_usd"] for e in recent
                                   if e["side"] == "SHORT_LIQ"), 2),
        }

    # ------------------------------------------------------------- stream

    def ensure_started(self) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._thread = threading.Thread(
                target=self._thread_main, name="liq-feed", daemon=True)
            self._thread.start()

    def _thread_main(self) -> None:
        try:
            asyncio.run(self._run())
        except Exception as e:           # noqa: BLE001
            self.last_error = f"{type(e).__name__}: {e}"[:200]
            self.connected = False

    async def _run(self) -> None:
        import websockets               # uvicorn[standard] 동봉 — 지연 임포트
        backoff = 5.0
        while True:
            try:
                async with websockets.connect(
                        WS_URL, ping_interval=20, close_timeout=5) as ws:
                    self.connected = True
                    self.last_error = ""
                    backoff = 5.0
                    async for raw in ws:
                        evt = _parse(raw)
                        if evt is not None:
                            self._ingest(evt)
            except Exception as e:       # noqa: BLE001 — 재연결 백오프
                self.connected = False
                self.last_error = f"{type(e).__name__}: {e}"[:200]
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60.0)

    # -------------------------------------------------------------- views

    def status(self, limit: int = 40) -> dict:
        with self._lock:
            rows = list(self._events)[-limit:][::-1]
        return {"connected": self.connected, "last_error": self.last_error,
                "min_notional_usd": _MIN_NOTIONAL,
                "hour": self._hour_totals(), "events": rows}


FEED = LiquidationFeed()

router = APIRouter(prefix="/api/trading", tags=["liquidations"])


@router.get("/liquidations")
def liquidations(limit: int = 40):
    """최근 강제청산 이벤트 (표시 전용 — 매매 판단에 미사용). 첫 호출이
    스트림을 지연 기동한다; 연결 실패는 connected=false 로만 드러난다."""
    FEED.ensure_started()
    return FEED.status(limit=max(1, min(limit, _BUF_MAX)))
