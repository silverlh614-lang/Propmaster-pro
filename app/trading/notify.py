"""@responsibility 텔레그램 알림 — 매매 이벤트를 단방향 푸시하는 fire-and-forget 알람기 (계정 연동·수신 명령 없음)

One-way Telegram push notifier for trade events (paper engine).

A pure ALARM: it consumes the journal rows the position FSM already emits
(OPEN / CLOSE / PARTIAL / ADD) and pushes a short Korean message to a
Telegram chat. It never receives commands, never touches the account or the
risk gate, and never blocks or raises into the trading loop — sends run on a
background daemon thread and every failure is swallowed.

Enable by setting TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID (env). Absent
either, the notifier is a no-op. TELEGRAM_ALERT_EVENTS (default "OPEN,CLOSE")
picks which lifecycle events fire a push.
"""
from __future__ import annotations

import os
import queue
import threading

import httpx

_API_URL = "https://api.telegram.org/bot{token}/sendMessage"
_SEND_TIMEOUT = 10.0
_QUEUE_MAX = 100

# 진입 방향 라벨: LONG = 매수, SHORT = 매도 (사용자가 요구한 "매수/매도" 알림)
_SIDE_LABEL = {"LONG": "🟢 매수 / LONG", "SHORT": "🔴 매도 / SHORT"}
_RESULT_LABEL = {"WIN": "✅ 익절", "LOSS": "🛑 손절", "CLOSED": "⚪ 청산"}


def _money(v) -> str:
    try:
        return f"{float(v):+.2f} USDT"
    except (TypeError, ValueError):
        return str(v)


def _r(v) -> str:
    try:
        return f"{float(v):+.2f}R"
    except (TypeError, ValueError):
        return str(v)


class TelegramNotifier:
    """Fire-and-forget Telegram sender. Construction reads env by default;
    args override for tests. `notify_trade` is wired as the Journal sink."""

    def __init__(self, token: str | None = None, chat_id: str | None = None,
                 events: str | None = None):
        self.token = (token if token is not None
                      else os.getenv("TELEGRAM_BOT_TOKEN", "")).strip()
        self.chat_id = (chat_id if chat_id is not None
                        else os.getenv("TELEGRAM_CHAT_ID", "")).strip()
        raw = (events if events is not None
               else os.getenv("TELEGRAM_ALERT_EVENTS", "OPEN,CLOSE"))
        self.events = {e.strip().upper() for e in raw.split(",") if e.strip()}
        self._q: "queue.Queue[str]" = queue.Queue(maxsize=_QUEUE_MAX)
        self._worker: threading.Thread | None = None
        self._lock = threading.Lock()

    @property
    def enabled(self) -> bool:
        return bool(self.token and self.chat_id)

    # ------------------------------------------------------------- sink API

    def notify_trade(self, row: dict) -> None:
        """Journal sink — invoked on every appended row. Filters by event and
        enqueues a formatted push. Never raises (trading must not depend on it)."""
        try:
            if not self.enabled:
                return
            if str(row.get("event", "")).upper() not in self.events:
                return
            self._enqueue(self.format(row))
        except Exception:
            pass

    def send_text(self, text: str) -> None:
        """Direct push (e.g. the /notify/test probe). No-op when disabled."""
        if self.enabled:
            self._enqueue(text)

    # ------------------------------------------------------------- format

    @staticmethod
    def format(row: dict) -> str:
        """Render one journal row as a short plain-text alert. Plain text (no
        HTML) so free-form notes never need escaping."""
        event = str(row.get("event", "")).upper()
        sym = row.get("symbol", "?")
        mode = row.get("mode", "paper")
        if event == "OPEN":
            head = _SIDE_LABEL.get(str(row.get("side", "")).upper(),
                                   row.get("side", ""))
            lines = [f"{head}  {sym}  진입",
                     f"진입가 {row.get('entry_price', '')}"]
            qty, lev = row.get("qty", ""), row.get("leverage", "")
            if qty not in ("", None):
                lines.append(f"수량 {qty}" + (f" · {lev}x" if lev not in ("", None) else ""))
            if row.get("risk_usd", "") not in ("", None):
                lines.append(f"리스크 {row.get('risk_usd')} USDT")
            strat, det = row.get("strategy", ""), row.get("signal_detail", "")
            if strat or det:
                lines.append(f"전략 {strat}" + (f" · {det}" if det else ""))
            lines.append(f"[{mode}]")
            return "\n".join(lines)
        if event in ("CLOSE", "PARTIAL"):
            res = str(row.get("result", "")).upper()
            label = ("🟡 부분익절" if event == "PARTIAL"
                     else _RESULT_LABEL.get(res, "⚪ 청산"))
            lines = [f"{label}  {sym}"]
            if row.get("exit_price", "") not in ("", None):
                lines.append(f"청산가 {row.get('exit_price')}")
            rr = row.get("r_multiple", "")
            tail = f" ({_r(rr)})" if rr not in ("", None) else ""
            lines.append(f"손익 {_money(row.get('pnl_usd', ''))}{tail}")
            if row.get("reason", ""):
                lines.append(f"사유 {row.get('reason')}")
            lines.append(f"[{mode}]")
            return "\n".join(lines)
        if event == "ADD":
            head = _SIDE_LABEL.get(str(row.get("side", "")).upper(),
                                   row.get("side", ""))
            return (f"➕ 애드업 {head}  {sym}\n"
                    f"진입가 {row.get('entry_price', '')}\n[{mode}]")
        return f"{sym} {event} {row.get('side', '')}".strip()

    # ------------------------------------------------------------- dispatch

    def _enqueue(self, text: str) -> None:
        self._ensure_worker()
        try:
            self._q.put_nowait(text)
        except queue.Full:
            pass                    # backpressure: drop rather than block trading

    def _ensure_worker(self) -> None:
        with self._lock:
            if self._worker is None or not self._worker.is_alive():
                self._worker = threading.Thread(
                    target=self._run, name="telegram-notify", daemon=True)
                self._worker.start()

    def _run(self) -> None:
        while True:
            text = self._q.get()
            try:
                self._deliver(text)
            finally:
                self._q.task_done()

    def _deliver(self, text: str) -> None:
        """The single network call. Isolated so tests can exercise it directly;
        swallows every error so a dead network never surfaces here."""
        try:
            httpx.post(_API_URL.format(token=self.token),
                       json={"chat_id": self.chat_id, "text": text,
                             "disable_web_page_preview": True},
                       timeout=_SEND_TIMEOUT)
        except Exception:
            pass
