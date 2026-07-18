"""@responsibility 단일 심볼 봇 — 자체 kline 수집·포지션 매니저·종가 결정 루프, 공유 리스크·저널을 주입받는 페이퍼 전용

Per-symbol trading bot.

SymbolBot drives one symbol (BTCUSDT ...): its own kline collector, position
manager and closed-bar decision loop. Risk manager, journal, position store,
ledger and the prop mark-tick are injected by TradingManager so every bot
shares one global risk gate / kill switch (invariant: single risk gate).

Phase 1: mode is always "paper" — no real order path exists yet.
"""
from __future__ import annotations

import asyncio
import time

from .account import AccountLedger
from .config import TradingConfig, SymbolSpec, strategy_for
from .collectors.kline import KlineCollector
from .execution.position import PositionManager
from .indicators import atr, ema
from .risk import RiskManager
from .store import Journal, PositionStore
from .watch import proximity_scan
from .strategies import STRATEGIES, make_strategy
from .strategies.base import TradingContext


class SymbolBot:
    def __init__(self, spec: SymbolSpec, cfg: TradingConfig,
                 journal: Journal, risk: RiskManager,
                 pos_store: PositionStore, ledger: AccountLedger,
                 prop_tick=None, notifier=None, signal_journal=None):
        self.spec = spec
        self.cfg = cfg
        self.journal = journal
        self.risk = risk
        self.pos_store = pos_store
        self.ledger = ledger
        self._prop_tick = prop_tick   # manager callback: one rule-engine mark
        self.notifier = notifier      # 실시간 근접 알림용 (proximity_scan)
        self.signal_journal = signal_journal   # 전략 시그널 영속 기록 (체결 무관)
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
        self._last_signal_side: str | None = None   # 시그널 알림 에피소드 디둡
        self._stop = asyncio.Event()
        self._tasks: list[asyncio.Task] = []
        # Feed runs independent of trading so the live chart warms while stopped.
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
        proximity_scan(self)            # 실시간 목표가·손절가 근접 알림 (매 폴링)
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
        self._resolve_signals(bar)      # 기록된 시그널의 전진(목표/손절) 판정

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
            self._signal_alert(None, "")    # reset dedup, no push
            self.note = self.pm.note if self.pm.pos else "watching — no signal"
            return
        price = bar.close
        p = self.pm.pos
        if p and p.state.value == "OPEN" and sig.side is p.side:
            self.pm.try_add(sig, price, atr_val or 0.0, ctx.now)
        elif not (p and p.state.value == "OPEN"):
            self.pm.try_open(sig, price, atr_val or 0.0, ctx.now)
        self.note = self.pm.note
        # 조건 충족 즉시 푸시 (체결과 무관) — 진입 시도 뒤라 차단 사유를 함께 싣는다
        self._signal_alert(sig, self.note)

    def _resolve_signals(self, bar) -> None:
        """이 종목의 미결 시그널을 방금 닫힌 봉의 고가/저가로 판정한다 — 목표/손절
        중 뭘 먼저 쳤는지(전진 검증). 체결과 무관. 실패는 매매에 영향 없음."""
        if self.signal_journal is None:
            return
        iv = self.cfg.entry_interval
        bar_seconds = int(iv) * 60 if iv.isdigit() else 3600
        now_ts = bar.ts_ms / 1000 + bar_seconds        # 이 봉의 종료 시각
        try:
            self.signal_journal.resolve_open(
                self.spec.key, bar.high, bar.low, now_ts, bar_seconds,
                self.cfg.signal_timeout_bars)
        except Exception:                # noqa: BLE001 — 기록 판정은 매매를 막지 않는다
            pass

    def _signal_alert(self, sig, note: str = "") -> None:
        """전략 조건이 충족되는 순간 텔레그램으로 시그널을 즉시 푸시한다 — 실제 체결
        (리스크 관문·동시포지션·예산 캡에 막힐 수 있음)과 무관하게. 목표가는 rr_target
        기하로 계산하고, 진입이 막혔으면 그 사유를 함께 싣는다. 에피소드 디둡: 같은
        방향이 여러 봉 이어져도 1회, 방향 전환·재출현 시 재발송. 알림 실패는 매매 무영향."""
        side = sig.side.value if sig else None
        if side == self._last_signal_side:
            return
        self._last_signal_side = side
        if side is None:                 # 시그널 소멸 — 디둡만 리셋, 기록/발송 없음
            return
        entry, stop = sig.entry_hint, sig.stop_price
        target = None
        rr = getattr(self.cfg, "rr_target", 0.0)
        if rr and entry is not None and stop is not None:
            risk = abs(entry - stop)
            target = entry + risk * rr if side == "LONG" else entry - risk * rr
        blocked = ""
        if note and "blocked" in note:              # "entry blocked: <reason>"
            blocked = note.split("blocked:", 1)[-1].strip() if "blocked:" in note \
                else note
        # 1) 영속 기록 — 체결 여부와 무관하게 모든 시그널을 남긴다 (기록이 본체)
        if self.signal_journal is not None:
            try:
                self.signal_journal.append({
                    "symbol": self.spec.key, "strategy": self.strategy_name,
                    "side": side, "signal_type": sig.signal_type,
                    "entry": sig.entry_hint, "target": target,
                    "stop": sig.stop_price, "detail": sig.detail,
                    "blocked": blocked, "entered": not blocked})
            except Exception:            # noqa: BLE001 — 기록 실패가 매매를 막지 않는다
                pass
        # 2) 텔레그램 푸시
        if self.notifier is not None:
            try:
                self.notifier.notify_signal(self.spec.key, self.strategy_name,
                                            sig, target=target, blocked=blocked)
            except Exception:            # noqa: BLE001 — 알림은 매매를 막지 않는다
                pass

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
