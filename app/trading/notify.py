"""@responsibility 텔레그램 알림 — 매매 이벤트를 단방향 푸시하는 fire-and-forget 알람기 (계정 연동·수신 명령 없음)

One-way Telegram push notifier for trade events (paper engine).

A pure ALARM: it consumes the journal rows the position FSM already emits
(OPEN / CLOSE / PARTIAL / ADD) and pushes a short Korean message to a
Telegram chat. It never receives commands, never touches the account or the
risk gate, and never blocks or raises into the trading loop — sends run on a
background daemon thread and every failure is swallowed.

Enable by setting TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID (env). Absent
either, the notifier is a no-op. TELEGRAM_ALERT_EVENTS (default
"OPEN,CLOSE,PARTIAL,SIGNAL") picks which pushes fire. SIGNAL is special: it is
NOT a journal row — it fires the moment a strategy's entry condition is met,
decoupled from the paper account, so a signal blocked by the risk gate (budget /
concurrent caps) still alerts. OPEN/CLOSE remain the actual paper fills.
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
_SEP = "━━━━━━━━━━"


def _money(v) -> str:
    try:
        return f"{float(v):+.2f} USDT"
    except (TypeError, ValueError):
        return str(v)


def _amt(v) -> str:
    try:
        return f"{float(v):.2f} USDT"
    except (TypeError, ValueError):
        return f"{v} USDT"


def _px(v) -> str:
    try:
        s = f"{float(v):.6f}".rstrip("0").rstrip(".")
        return s or "0"
    except (TypeError, ValueError):
        return str(v)


def _r(v) -> str:
    try:
        return f"{float(v):+.2f}R"
    except (TypeError, ValueError):
        return str(v)


def _qty(v) -> str:
    """수량 표기 — 불필요한 소수 0 을 떼고 읽기 쉽게 (예: 1192.7, 24450, 0.5)."""
    try:
        return f"{float(v):g}"
    except (TypeError, ValueError):
        return str(v)


def _risk_line(risk_pct, risk_usd) -> str:
    """'예상 리스크 ≈X% ($Y)' 한 줄 — 값이 없으면 빈 문자열(줄 생략)."""
    try:
        p = float(risk_pct) if risk_pct not in ("", None) else None
        u = float(risk_usd) if risk_usd not in ("", None) else None
    except (TypeError, ValueError):
        return ""
    if p is None and u is None:
        return ""
    pct = f"≈{p:g}%" if p is not None else ""
    usd = f" (${u:g})" if u is not None else ""
    return f"예상리스크 {pct}{usd}".rstrip()


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
               else os.getenv("TELEGRAM_ALERT_EVENTS", "OPEN,CLOSE,PARTIAL,SIGNAL"))
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

    def notify_signal(self, symbol: str, strategy: str, sig,
                      target=None, blocked: str = "",
                      risk_pct=None, risk_usd=None) -> None:
        """전략 조건 충족 즉시 푸시 — 실제 체결과 무관(리스크 관문에 막혀도 발송).
        'SIGNAL' 이벤트가 켜져 있고 활성일 때만. 저널 sink 를 안 거친다. 진입이
        막혔으면 blocked 사유를, 목표가는 target, 예상 리스크는 risk_pct/risk_usd 를
        함께 싣는다."""
        try:
            if not self.enabled or "SIGNAL" not in self.events:
                return
            self._enqueue(self.format_signal(
                symbol, strategy, sig.side.value, sig.detail,
                sig.entry_hint, sig.stop_price, target=target, blocked=blocked,
                risk_pct=risk_pct, risk_usd=risk_usd))
        except Exception:
            pass

    # ------------------------------------------------------------- format

    @staticmethod
    def format_signal(sym: str, strategy: str, side: str, detail: str,
                      entry, stop, target=None, blocked: str = "",
                      risk_pct=None, risk_usd=None) -> str:
        """전략 시그널(조건 충족)을 짧은 알림으로 렌더. 기준가·목표가·손절가·예상
        리스크를 싣고, 진입이 리스크 관문에 막혔으면 그 사유를 표시한다. 예상 리스크는
        진입 전 프롭 예산 기반 추정(포지션이 열려 있으면 실제 initial_risk)이다."""
        head = _SIDE_LABEL.get(str(side).upper(), side)
        lines = [f"🔔 시그널  {head}  {sym}", _SEP, f"전략   {strategy}"]
        if detail:
            lines.append(f"트리거 {detail}")
        if entry not in ("", None):
            lines.append(f"기준가 {_px(entry)}")
        if target not in ("", None):
            lines.append(f"목표가 {_px(target)}")
        if stop not in ("", None):
            lines.append(f"손절가 {_px(stop)}")
        rl = _risk_line(risk_pct, risk_usd)
        if rl:
            lines.append(rl)
        lines.append(f"⛔ 차단   {blocked}" if blocked else "※ 조건 충족 신호")
        return "\n".join(lines)

    @staticmethod
    def format(row: dict) -> str:
        """Render one journal row as a short plain-text alert. Plain text (no
        HTML) so free-form notes never need escaping."""
        event = str(row.get("event", "")).upper()
        sym = row.get("symbol", "?")
        if event == "OPEN":
            head = _SIDE_LABEL.get(str(row.get("side", "")).upper(),
                                   row.get("side", ""))
            lines = [f"{head}  {sym}  진입", _SEP,
                     f"진입가   {_px(row.get('entry_price'))}"]
            if row.get("target_price", "") not in ("", None):
                lines.append(f"목표가   {_px(row.get('target_price'))}")
            if row.get("stop_price", "") not in ("", None):
                lines.append(f"손절가   {_px(row.get('stop_price'))}")
            qty, lev = row.get("qty", ""), row.get("leverage", "")
            if qty not in ("", None):
                lines.append(f"수량     {qty}"
                             + (f" ({lev}x)" if lev not in ("", None) else ""))
            if row.get("risk_usd", "") not in ("", None):
                lines.append(f"리스크   {_amt(row.get('risk_usd'))}")
            return "\n".join(lines)
        if event in ("CLOSE", "PARTIAL"):
            res = str(row.get("result", "")).upper()
            head = (f"🎯 목표가 도달 · 부분익절  {sym}" if event == "PARTIAL"
                    else f"{_RESULT_LABEL.get(res, '⚪ 청산')}  {sym}")
            lines = [head, _SEP]
            if row.get("exit_price", "") not in ("", None):
                lines.append(f"청산가   {_px(row.get('exit_price'))}")
            # 부분익절 행은 이번에 청산한 수량을 싣는다 (전량 청산 행엔 qty 없음)
            if event == "PARTIAL" and row.get("qty", "") not in ("", None):
                lines.append(f"익절수량 {_qty(row.get('qty'))}")
            rr = row.get("r_multiple", "")
            tail = f" ({_r(rr)})" if rr not in ("", None) else ""
            lines.append(f"손익     {_money(row.get('pnl_usd', ''))}{tail}")
            if event == "CLOSE" and row.get("reason", ""):
                lines.append(f"사유     {row.get('reason')}")
            return "\n".join(lines)
        if event == "ADD":
            head = _SIDE_LABEL.get(str(row.get("side", "")).upper(),
                                   row.get("side", ""))
            return (f"➕ 애드업  {head}  {sym}\n"
                    f"진입가   {_px(row.get('entry_price'))}")
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
