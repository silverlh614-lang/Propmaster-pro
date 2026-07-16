"""@responsibility 트레이딩 오케스트레이션 — SymbolBot 수명주기 + TradingManager 전역 리스크·저널 공유, 페이퍼 전용

Trading engine orchestration.

SymbolBot     — one symbol (BTCUSDT ...): its own kline collector, position
                manager and closed-bar decision loop.
TradingManager  — starts/stops all enabled SymbolBots together; shares one
                journal, one risk manager (global daily caps / kill switch)
                and one persisted state blob (auto-resume).

Phase 1: mode is always "paper" — live mode is refused until the Phase 3
gate lands. No real order path exists yet.

Prop layer: TradingManager owns the PropDesk. Every closed bar feeds one
mark-to-market tick into the challenge account's rule engine (prop_tick);
a breach trips the kill switch and flattens all paper positions.
"""
from __future__ import annotations

import asyncio
import time

from ..prop.desk import PropDesk
from .account import AccountLedger
from .config import (CONFIG, SYMBOL_SPECS, TradingConfig, SymbolSpec,
                     enabled_symbols)
from .collectors.kline import KlineCollector
from .execution.position import PositionManager
from .indicators import atr, ema
from .models import Side
from .risk import RiskManager
from .store import AccountStore, BotState, Journal, PositionStore
from .config import strategy_for
from .discovery import AutoDiscovery
from .execution.broker import make_broker
from .strategies import STRATEGIES, make_strategy
from .strategies.base import TradingContext


class SymbolBot:
    def __init__(self, spec: SymbolSpec, cfg: TradingConfig,
                 journal: Journal, risk: RiskManager,
                 pos_store: PositionStore, ledger: AccountLedger,
                 prop_tick=None):
        self.spec = spec
        self.cfg = cfg
        self.journal = journal
        self.risk = risk
        self.pos_store = pos_store
        self.ledger = ledger
        self._prop_tick = prop_tick   # manager callback: one rule-engine mark
        self.collector = KlineCollector(spec.symbol, cfg.entry_interval,
                                        cfg.htf_interval, cfg.warmup_bars)
        self.mode = "paper"
        self.strategy_name = "prop_breakout"
        self.strategy = None
        self.pm: PositionManager | None = None
        self.running = False
        self.note = "stopped"
        self._last_bar_ts: int | None = None
        self._last_pos_state: dict | None = None
        self._stop = asyncio.Event()
        self._tasks: list[asyncio.Task] = []
        # The kline feed runs independent of trading so the live chart warms up
        # even while the bot is stopped.
        self._feed_stop = asyncio.Event()
        self._feed_task: asyncio.Task | None = None

    def start_feed(self) -> None:
        """Start the market-data feed (idempotent). Warms candles regardless of
        whether the trading loop is running."""
        if self._feed_task is not None and not self._feed_task.done():
            return
        self._feed_stop = asyncio.Event()
        self._feed_task = asyncio.create_task(
            self.collector.run(self._feed_stop, self.cfg.poll_sec),
            name=f"kline-{self.spec.key}")

    async def stop_feed(self) -> None:
        if self._feed_task is None:
            return
        self._feed_stop.set()
        self._feed_task.cancel()
        await asyncio.gather(self._feed_task, return_exceptions=True)
        self._feed_task = None

    async def start(self, mode: str, strategy: str) -> None:
        if self.running:
            return
        self.mode = mode
        # per-symbol strategy: the gate found majors want Donchian, choppy
        # alts want the volatility breakout — TRADING_SYMBOL_STRATEGY maps it.
        resolved = strategy_for(self.spec.key, strategy)
        if resolved not in STRATEGIES:
            resolved = strategy
        self.strategy_name = resolved
        self.strategy = make_strategy(resolved, self.cfg)
        self.pm = PositionManager(self.spec, self.cfg, self.risk, self.journal,
                                  mode, resolved, ledger=self.ledger)
        # Restore any live position from the last run so a trade in progress
        # survives a restart / redeploy (equity restores via the ledger).
        self.pm.load_state(self.pos_store.load(self.spec.key))
        self._last_pos_state = self.pm.to_state()
        self._stop = asyncio.Event()
        self._last_bar_ts = None
        self.running = True
        self.note = "starting"
        self.start_feed()                       # ensure market data is flowing
        self._tasks = [
            asyncio.create_task(self._decision_loop(),
                                name=f"loop-{self.spec.key}"),
        ]

    def _persist_pos(self) -> None:
        """Snapshot the position/equity after a change so it survives restarts."""
        if self.pm is None:
            return
        st = self.pm.to_state()
        if st != self._last_pos_state:
            self.pos_store.save(self.spec.key, st)
            self._last_pos_state = st

    async def shutdown(self) -> None:
        # Stop only the trading loop; the feed keeps the chart live.
        if not self.running:
            return
        self._stop.set()
        for t in self._tasks:
            t.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks = []
        self.running = False
        self.note = "stopped"

    def manual(self, action: str) -> dict:
        """Control-tower close button (paper). Entries stay strategy-driven —
        prop discipline is to follow the system, not hand-trade."""
        if not self.pm:
            return {"ok": False, "error": f"no {self.spec.key} session"}
        if action == "close":
            p = self.pm.pos
            px = self.collector.last_price()
            if not p or px is None:
                return {"ok": False, "error": "no open position"}
            self.pm._close(px, "manual close", time.time())
            self._persist_pos()
            return {"ok": True, "pnl_usd": p.realized_pnl_usd}
        return {"ok": False, "error": f"unknown action {action}"}

    # ---------------------------------------------------------------- loop

    async def _decision_loop(self) -> None:
        while not self._stop.is_set():
            try:
                self._step(self.strategy)
                self.risk.record_ok()
                self._persist_pos()             # survive restarts/redeploys
            except Exception as e:
                self.note = f"loop error: {type(e).__name__}: {e}"
                self.risk.record_error(f"{self.spec.key}: {e}")
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.cfg.poll_sec)
            except asyncio.TimeoutError:
                pass

    def _step(self, strategy) -> None:
        """Act once per newly-closed entry candle: manage the open position
        against the bar, then let the strategy open or pyramid."""
        assert self.pm is not None
        entry = self.collector.entry_closed()
        if not entry:
            self.note = f"warming up ({self.collector.status()['entry_bars']} bars)"
            return
        bar = entry[-1]
        if bar.ts_ms == self._last_bar_ts:
            self.note = self.pm.note      # no new bar; keep managing view fresh
            return
        self._last_bar_ts = bar.ts_ms
        atr_val = atr(entry, self.cfg.atr_period)

        self.pm.flatten_if_closed()
        self.pm.manage(bar, atr_val)
        if self._reset_guard_hits(bar.ts_ms):
            p = self.pm.pos
            if p and p.state.value == "OPEN":
                px = self.collector.last_price() or bar.close
                self.pm._close(px, "pre-reset flatten (00:30 UTC guard)",
                               time.time())
                self._persist_pos()
        if self._prop_tick is not None:
            # judge the challenge account on this bar's mark-to-market equity
            # BEFORE any new entry — a breached account never trades again
            self._prop_tick()

        ctx = self._build_ctx(entry)
        sig = strategy.evaluate(ctx)
        if sig is None:
            self.note = self.pm.note if self.pm.pos else "watching — no signal"
            return
        price = bar.close
        p = self.pm.pos
        if p and p.state.value == "OPEN" and sig.side is p.side:
            self.pm.try_add(sig, price, atr_val or 0.0, ctx.now)
        elif not (p and p.state.value == "OPEN"):
            self.pm.try_open(sig, price, atr_val or 0.0, ctx.now)
        self.note = self.pm.note

    def _reset_guard_hits(self, bar_ts_ms: int) -> bool:
        """True when this just-closed bar sits inside the flatten window that
        ends at the next 00:30 UTC daily reset (0 disables). Keeps the
        challenge from carrying open unrealized loss across the re-anchor."""
        win = self.cfg.flatten_before_reset_min
        if win <= 0:
            return False
        # minutes-of-day of the bar's CLOSE, relative to the 00:30 boundary
        import datetime as _dt
        entry_min = int(self.cfg.entry_interval) if             self.cfg.entry_interval.isdigit() else 1440
        close_ts = bar_ts_ms / 1000 + entry_min * 60
        secs = _dt.datetime.fromtimestamp(close_ts, _dt.timezone.utc)
        mins_since_reset = ((secs.hour * 60 + secs.minute) - 30) % 1440
        mins_to_reset = (1440 - mins_since_reset) % 1440
        return mins_to_reset <= win

    def _build_ctx(self, entry: list) -> TradingContext:
        return TradingContext(
            symbol=self.spec.key,
            htf_candles=self.collector.htf_closed(),
            entry_candles=entry,
            equity_usd=self.pm.equity if self.pm else self.cfg.equity_usd,
            now=time.time(),
            open_position_side=(self.pm.pos.side.value
                                if self.pm and self.pm.pos
                                and self.pm.pos.state.value == "OPEN" else None),
        )

    # ---------------------------------------------------------------- view

    def diagnostics(self) -> dict | None:
        """Live entry-gate snapshot for the dashboard gauge (real data)."""
        entry = self.collector.entry_closed()
        if not entry or self.strategy is None:
            return None
        try:
            return self.strategy.diagnose(self._build_ctx(entry))
        except Exception:
            return None

    def candles(self, tf: str = "entry", limit: int = 120) -> dict:
        cs = (self.collector.htf_closed() if tf == "htf"
              else self.collector.entry_closed())[-limit:]
        closes = [c.close for c in cs]
        line = ema(closes, self.cfg.ema_period) if closes else []
        return {"symbol": self.spec.key, "tf": tf,
                "interval": self.cfg.htf_interval if tf == "htf" else self.cfg.entry_interval,
                "candles": [[c.ts_ms, c.open, c.high, c.low, c.close, c.volume] for c in cs],
                "ema": [round(x, 6) for x in line]}

    def status(self) -> dict:
        px = self.collector.last_price()
        return {
            "symbol": self.spec.key,
            "strategy": self.strategy_name,
            "running": self.running,
            "note": self.note,
            "collector": self.collector.status(),
            "manager": self.pm.snapshot(px) if self.pm else None,
            "diagnostics": self.diagnostics(),
        }


class TradingManager:
    def __init__(self, cfg: TradingConfig = CONFIG):
        self.cfg = cfg
        self.journal = Journal()
        self.state_store = BotState()
        self.pos_store = PositionStore()
        # 한 계좌 원칙: 전 심볼이 이 원장 하나에서 돈이 나간다. 계좌 레코드가
        # 아직 없으면 운영 심볼(BTC)의 레거시 심볼별 equity 를 1회 승계한다.
        self.ledger = AccountLedger(cfg, AccountStore(),
                                    legacy_equity=self.pos_store.load("BTC")
                                    .get("equity"))
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
                         prop_tick=self.prop_tick)

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
        future entries) and flatten every open paper position now."""
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
