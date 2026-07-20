"""@responsibility 봇 헬스 워치독 — 피드 정체·결정루프 오류를 주기 점검해 텔레그램으로 이상/회복 1회 알림, 매매 무영향

Bot health watchdog (paper engine, side-channel only).

A background monitor that periodically inspects every SymbolBot and pushes a
Telegram alert when something looks wrong — so an operator watching only their
phone is TOLD the bot stalled, instead of having to poll /status by hand. It
never touches the trading loop, risk gate, or account: it reads bot.status()
and sends text, nothing more (every failure is swallowed).

Two signals, both derived from the existing status snapshot:
  · feed_stalled — the kline collector's poll count did not advance over one
    check interval (both providers failing, or the feed task died).
  · loop_error  — the decision loop is raising each cycle (note "loop error:").

Alerts are edge-triggered with recovery: one message when a problem appears,
one when it clears. State is per-symbol so a persistent fault never spams.
"""
from __future__ import annotations

import asyncio


class HealthMonitor:
    def __init__(self, manager, notifier=None, interval_sec: int = 300,
                 enabled: bool = True):
        self.manager = manager
        self.notifier = notifier
        self.interval = interval_sec
        self.enabled = enabled
        self._prev_polls: dict[str, int] = {}   # symbol -> last seen poll count
        self._active: dict[str, set[str]] = {}   # symbol -> active problem codes
        self._last: dict[str, dict] = {}         # cached snapshot for /status
        self._task: asyncio.Task | None = None
        self._stop: asyncio.Event | None = None

    # ------------------------------------------------------------- evaluation

    @staticmethod
    def _problems(st: dict, poll_delta: int | None) -> list[tuple[str, str]]:
        """(code, 사람이 읽는 사유) 목록. poll_delta 는 이번 점검 구간의 폴 증가분
        (None = 최초 점검, 기준선만 잡고 정체 판정 보류)."""
        probs: list[tuple[str, str]] = []
        note = str(st.get("note", ""))
        coll = st.get("collector") or {}
        if note.startswith("loop error"):
            probs.append(("loop_error", f"결정 루프 오류 — {note[:120]}"))
        if poll_delta is not None and poll_delta <= 0:
            src = coll.get("source") or "none"
            probs.append(("feed_stalled",
                          f"시세 피드 무응답 (source={src}) — 수집 정지·양 provider 실패 의심"))
        return probs

    def check(self) -> dict:
        """One watchdog pass: evaluate every bot, emit edge-triggered alerts,
        cache the snapshot. Returns a small summary (also handy for tests)."""
        alerts: list[tuple[str, str, str]] = []   # (kind, symbol, message)
        snap: dict[str, dict] = {}
        for k, b in self.manager.bots.items():
            try:
                st = b.status()
            except Exception:                     # noqa: BLE001 — 헬스가 매매를 막지 않는다
                continue
            polls = int(((st.get("collector") or {}).get("polls")) or 0)
            prev = self._prev_polls.get(k)
            delta = None if prev is None else polls - prev
            self._prev_polls[k] = polls

            probs = self._problems(st, delta)
            codes = {c for c, _ in probs}
            was = self._active.get(k, set())
            for c, msg in probs:
                if c not in was:                  # 새로 발생 → 이상 알림
                    alerts.append(("problem", k, msg))
            for c in was - codes:                 # 해소 → 회복 알림
                alerts.append(("recovery", k, ""))
            self._active[k] = codes
            snap[k] = {"ok": not codes, "problems": sorted(codes)}
        self._last = snap
        self._emit(alerts)
        return {"checked": len(snap), "alerts": len(alerts), "bots": snap}

    def snapshot(self) -> dict:
        """Last computed health (no side effects) — surfaced in /status."""
        return dict(self._last)

    # ------------------------------------------------------------- dispatch

    def _emit(self, alerts: list[tuple[str, str, str]]) -> None:
        """Aggregate a whole pass into ONE Telegram message (a network outage
        that stalls 20 feeds must not fire 20 pushes). No-op if unconfigured."""
        if not alerts or self.notifier is None or not getattr(
                self.notifier, "enabled", False):
            return
        probs = [a for a in alerts if a[0] == "problem"]
        recs = [a for a in alerts if a[0] == "recovery"]
        lines: list[str] = []
        if probs:
            lines.append("⚠️ 봇 이상 감지")
            lines += [f"· {k}: {msg}" for _, k, msg in probs]
        if recs:
            if lines:
                lines.append("")
            lines.append("✅ 봇 회복 — 정상 복귀")
            lines += [f"· {k}" for _, k, _m in recs]
        try:
            self.notifier.send_text("\n".join(lines))
        except Exception:                         # noqa: BLE001 — 알림 실패가 매매를 막지 않는다
            pass

    # ------------------------------------------------------------- lifecycle

    async def run(self, stop: asyncio.Event) -> None:
        """Poll until stopped; never raises out (a dead watchdog must not take
        the app down)."""
        while not stop.is_set():
            try:
                self.check()
            except Exception:                     # noqa: BLE001
                pass
            try:
                await asyncio.wait_for(stop.wait(), timeout=self.interval)
            except asyncio.TimeoutError:
                pass

    def start(self) -> None:
        """Start the watchdog task (idempotent). No-op when disabled."""
        if not self.enabled or self.interval <= 0:
            return
        if self._task is not None and not self._task.done():
            return
        self._stop = asyncio.Event()
        self._task = asyncio.create_task(self.run(self._stop), name="health-monitor")

    async def stop(self) -> None:
        if self._task is None:
            return
        if self._stop is not None:
            self._stop.set()
        self._task.cancel()
        await asyncio.gather(self._task, return_exceptions=True)
        self._task = None
