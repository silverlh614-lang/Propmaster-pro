"""@responsibility 트레이딩 오케스트레이션 — TradingManager 가 전 SymbolBot 을 조립·기동하고 전역 리스크·저널·프롭 데스크를 공유, 페이퍼 전용

Trading engine orchestration.

TradingManager  — starts/stops all enabled SymbolBots together; shares one
                journal, one risk manager (global daily caps / kill switch)
                and one persisted state blob (auto-resume). SymbolBot itself
                lives in symbol_bot.py.

Phase 1: mode is always "paper" — live mode is refused until the Phase 3
gate lands. No real order path exists yet.

Prop layer: TradingManager owns the PropDesk. Every closed bar feeds one
mark-to-market tick into the challenge account's rule engine (prop_tick);
a breach trips the kill switch and flattens all paper positions.
"""
from __future__ import annotations

import time

from ..prop.desk import PropDesk
from .account import AccountLedger
from .config import (CONFIG, SYMBOL_SPECS, TradingConfig, SymbolSpec,
                     enabled_symbols)
from .risk import RiskManager
from .store import AccountStore, BotState, Journal, PositionStore, SignalJournal
from .discovery import AutoDiscovery
from .execution.broker import make_broker
from .notify import TelegramNotifier
from .symbol_bot import SymbolBot
from .strategies import STRATEGIES


class TradingManager:
    def __init__(self, cfg: TradingConfig = CONFIG):
        self.cfg = cfg
        self.notifier = TelegramNotifier()   # 텔레그램 단방향 알람 (기본 OFF)
        self.journal = Journal(sink=self.notifier.notify_trade)
        self.signals = SignalJournal()   # 전략 시그널 영속 기록 (체결과 무관)
        self.state_store = BotState()
        self.pos_store = PositionStore()
        # 한 계좌 원칙: 계좌 레코드가 없으면 운영 심볼(BTC) 레거시 equity 1회 승계.
        self.ledger = AccountLedger(cfg, AccountStore(),
            legacy_equity=self.pos_store.load("BTC").get("equity"))
        self.risk = RiskManager(cfg, self.journal, self.state_store)
        # 프롭 데스크: 챌린지 계좌 룰 엔진. 원장(잔고)·리스크 관문과 같은
        # 객체를 공유해야 하므로 여기(단일 조립점)서만 만든다.
        self.prop = PropDesk(ledger=self.ledger, on_breach=self._on_prop_breach)
        self.risk.attach_prop(self.prop)
        # 코어 = 게이트 검증 종목(TRADING_SYMBOLS). 수동 = 토글 UI 로 켠 위성.
        # 부팅 로스터 = 코어 ∪ (재시작 전 저장된 수동 선택).
        self._core = [s.key for s in enabled_symbols()]
        self._manual = [k for k in self.state_store.load().get("manual_symbols", [])
                        if k in SYMBOL_SPECS and k not in self._core]
        self.bots: dict[str, SymbolBot] = {}
        for k in self._core + self._manual:
            if k in SYMBOL_SPECS:
                self.bots[k] = self._make_bot(SYMBOL_SPECS[k])
        self.mode = "paper"
        self.strategy_name = "prop_breakout"
        # 자동 종목 발굴 (기본 OFF): 코어·수동은 불변, 위성 슬롯만 로테이션.
        self.discovery = AutoDiscovery(self)
        self.broker = make_broker(cfg)   # Phase 3 배관 — 게이트 전엔 PaperBroker

    @property
    def core(self) -> list[str]:
        return list(self._core)

    def protected_keys(self) -> list[str]:
        """코어 + 수동 핀 — auto-discovery 로테이션이 절대 건드리지 않는 집합."""
        return list(dict.fromkeys(self._core + self._manual))

    async def toggle_symbol(self, key: str, active: bool) -> dict:
        """토글 UI: 후보 종목을 런타임에 켜거나(위성 봇 가동) 끈다(청산 상태만).
        코어는 항상 ON. 새 봇도 리스크 관문·전역 캡을 그대로 통과하므로 리스크는
        늘지 않고 기회만 는다. 선택은 상태에 저장돼 재시작에도 유지된다."""
        key = key.upper()
        if key not in SYMBOL_SPECS:
            return {"ok": False, "error": f"unknown symbol '{key}'"}
        if key in self._core:
            return {"ok": False, "error": f"'{key}' 는 코어 종목 — 항상 가동"}
        if active:
            if key not in self.bots:
                bot = self._make_bot(SYMBOL_SPECS[key])
                self.bots[key] = bot
                if self.running:
                    await bot.start(self.mode, self.strategy_name)
                else:
                    bot.start_feed()
            if key not in self._manual:
                self._manual.append(key)
        else:
            bot = self.bots.get(key)
            if (bot and bot.pm and bot.pm.pos
                    and bot.pm.pos.state.value == "OPEN"):
                return {"ok": False,
                        "error": f"'{key}' 열린 포지션 있음 — 먼저 청산하세요"}
            if bot is not None:
                await bot.shutdown()
                await bot.stop_feed()
                self.risk.unregister_book(key)
                self.bots.pop(key, None)
            if key in self._manual:
                self._manual.remove(key)
        st = self.state_store.load()
        st["manual_symbols"] = self._manual
        self.state_store.save(st)
        return {"ok": True, "symbol": key, "active": active,
                "symbols": list(self.bots)}

    def _make_bot(self, spec: SymbolSpec) -> SymbolBot:
        """Single SymbolBot factory — boot roster and discovery rotation both
        wire the same shared journal/risk/ledger/prop-tick objects."""
        return SymbolBot(spec, self.cfg, self.journal, self.risk,
                         self.pos_store, self.ledger,
                         prop_tick=self.prop_tick, notifier=self.notifier,
                         signal_journal=self.signals)

    @property
    def running(self) -> bool:
        return any(b.running for b in self.bots.values())

    async def start(self, mode: str = "paper",
                    strategy: str = "prop_breakout") -> dict:
        if self.running:
            return {"ok": False, "error": "already running"}
        if strategy not in STRATEGIES:
            return {"ok": False, "error": f"unknown strategy '{strategy}'"}
        if mode != "paper":
            return {"ok": False,
                    "error": "live mode is Phase 3 — only 'paper' is available"}
        self.mode = mode
        self.strategy_name = strategy
        for b in self.bots.values():
            await b.start(mode, strategy)
        self.discovery.start()          # no-op unless TRADING_AUTO_DISCOVERY
        st = self.state_store.load()
        st.update({"mode": mode, "strategy": strategy, "running": True})
        self.state_store.save(st)
        return {"ok": True, "mode": mode, "strategy": strategy,
                "symbols": list(self.bots)}

    def start_feeds(self) -> None:
        """Warm the live chart for every symbol without starting trading."""
        for b in self.bots.values():
            b.start_feed()

    async def stop_feeds(self) -> None:
        for b in self.bots.values():
            await b.stop_feed()

    async def shutdown(self) -> None:
        await self.discovery.stop()
        for b in self.bots.values():
            await b.shutdown()

    async def stop(self) -> dict:
        if not self.running:
            return {"ok": False, "error": "not running"}
        await self.shutdown()
        st = self.state_store.load()
        st["running"] = False
        self.state_store.save(st)
        return {"ok": True}

    def manual(self, action: str, symbol: str = "BTC") -> dict:
        bot = self.bots.get(symbol.upper())
        if bot is None:
            return {"ok": False, "error": f"symbol '{symbol}' not enabled"}
        return bot.manual(action)

    # ------------------------------------------------------------- prop

    def prop_mark_inputs(self) -> tuple[float, float, bool]:
        """(equity_mark, balance, flat) for the prop rule engine — equity is
        the ledger plus every symbol's unrealized PnL (breaches are judged on
        equity, targets on realized balance while flat)."""
        unreal = 0.0
        flat = True
        for b in self.bots.values():
            pm = b.pm
            if pm and pm.pos and pm.pos.state.value == "OPEN":
                flat = False
                px = b.collector.last_price()
                if px is not None:
                    unreal += pm.pos.unrealized_usd(px)
        bal = self.ledger.equity
        return bal + unreal, bal, flat

    def prop_tick(self) -> None:
        """One rule-engine mark (called each closed bar by any SymbolBot):
        equity/target judgement, conduct scan, then the guardian buffer —
        at the hard tier open positions are flattened BEFORE the real
        floor can terminate the account (the account itself survives)."""
        self.prop.on_mark(*self.prop_mark_inputs())
        self.prop.check_conduct(self.journal.tail(80))
        g = self.prop.guard_level()
        if g and g["level"] == "hard":
            self._flatten_all(f"prop guard buffer ({g['ratio']:.0%} left)")

    def _flatten_all(self, reason: str) -> None:
        """Close every open paper position at the last price."""
        for b in self.bots.values():
            pm = b.pm
            if pm and pm.pos and pm.pos.state.value == "OPEN":
                px = b.collector.last_price()
                if px is not None:
                    pm._close(px, reason, time.time())
                    b._persist_pos()

    def _on_prop_breach(self, reason: str) -> None:
        """Rule breach = account terminated: trip the kill switch (blocks all
        future entries) and flatten every open paper position now. Also push a
        distinct high-priority alert — a breach while FLAT produces no CLOSE
        rows, so the per-trade sink alone can miss the single most important
        event (the challenge is over). Notify BEFORE the flatten so the alert
        wins even if a close send is dropped."""
        try:
            self.notifier.send_text(
                "🚨 계좌 브리치 — 챌린지 종료 (터미널)\n"
                f"사유   {reason}\n"
                "킬스위치 트립 · 전 포지션 청산 · 신규 진입 차단")
        except Exception:                    # noqa: BLE001 — 알림 실패가 브리치 처리를 막지 않는다
            pass
        self.risk.trip(f"prop breach: {reason}")
        self._flatten_all(f"prop breach: {reason}")

    def candles(self, symbol: str, tf: str = "entry", limit: int = 120) -> dict:
        bot = self.bots.get(symbol.upper())
        if bot is None:
            return {"error": f"symbol '{symbol}' not enabled"}
        return bot.candles(tf, limit)

    def _account_view(self) -> dict:
        """한 계좌 관점의 스냅샷 — 전 심볼 열린 포지션의 미실현 합계와 전체
        실현 집계. 대시보드 ACCOUNT 패널은 탭과 무관하게 이것만 본다."""
        unreal = 0.0
        open_any = False
        for b in self.bots.values():
            pm = b.pm
            if pm and pm.pos and pm.pos.state.value == "OPEN":
                px = b.collector.last_price()
                if px is not None:
                    unreal += pm.pos.unrealized_usd(px)
                    open_any = True
        eq = round(self.ledger.equity, 4)
        return {
            "equity_usd": eq,
            "start_equity_usd": self.cfg.equity_usd,
            "unrealized_usd": round(unreal, 4) if open_any else None,
            "mark_value_usd": round(eq + unreal, 4),
            "aggregate": self.journal.aggregate(),
        }

    def status(self) -> dict:
        mark, bal, _flat = self.prop_mark_inputs()
        return {
            "running": self.running,
            "mode": self.mode,
            "strategy": self.strategy_name,
            "account": self._account_view(),
            "prop": self.prop.status(equity_mark=mark, balance=bal),
            "note": ("running: " + ",".join(self.bots)) if self.running else "stopped",
            "risk": self.risk.status(),
            "symbols": {k: b.status() for k, b in self.bots.items()},
            "discovery": self.discovery.status(),
            "execution": {"broker": self.broker.name, "live": self.broker.live},
            "by_symbol": self.journal.by_symbol(list(self.bots)),
            "server_time": time.time(),
        }


MANAGER = TradingManager()
