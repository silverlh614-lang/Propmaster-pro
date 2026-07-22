"""Offline tests for the Telegram trade-alert notifier — no network. Run:
python -m tests.test_notify

Delivery is monkeypatched (httpx.post replaced), so nothing leaves the box."""
from __future__ import annotations

import os
import tempfile

# isolate the journal CSV before importing the package
os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="notify-test-")

from app.trading import notify
from app.trading.notify import TelegramNotifier
from app.trading.store import Journal


def _open_row(side="LONG"):
    return {"event": "OPEN", "symbol": "ETH", "mode": "paper", "side": side,
            "strategy": "prop_breakout", "signal_detail": "donchian 55 break",
            "entry_price": 3421.5, "target_price": 3550.0, "stop_price": 3357.0,
            "qty": 0.03, "leverage": 3.0, "risk_usd": 2.0}


def _close_row(result="WIN"):
    return {"event": "CLOSE", "symbol": "ETH", "mode": "paper", "side": "LONG",
            "result": result, "exit_price": 3480.0, "pnl_usd": 5.32,
            "r_multiple": 2.66, "reason": "target 2R"}


# ------------------------------------------------------------- enabled gate

def test_enabled_gate():
    assert TelegramNotifier(token="", chat_id="").enabled is False
    assert TelegramNotifier(token="t", chat_id="").enabled is False
    assert TelegramNotifier(token="", chat_id="c").enabled is False
    assert TelegramNotifier(token="t", chat_id="c").enabled is True
    print("ok  enabled gate (needs both token and chat id)")


# ------------------------------------------------------------- formatting

def test_format_open():
    long_msg = TelegramNotifier.format(_open_row("LONG"))
    assert "매수" in long_msg and "ETH" in long_msg and "3421.5" in long_msg
    # 목표가·손절가 표시, 전략 줄 제거, [paper] 제거
    assert "목표가" in long_msg and "3550" in long_msg
    assert "손절가" in long_msg and "3357" in long_msg
    assert "paper" not in long_msg and "[" not in long_msg
    assert "전략" not in long_msg and "prop_breakout" not in long_msg
    short_msg = TelegramNotifier.format(_open_row("SHORT"))
    assert "매도" in short_msg
    print("ok  format OPEN (buy/sell, entry+target+stop, no strategy)")


def test_format_close():
    win = TelegramNotifier.format(_close_row("WIN"))
    assert "익절" in win and "+5.32 USDT" in win and "+2.66R" in win
    loss = TelegramNotifier.format({**_close_row("LOSS"), "pnl_usd": -1.9,
                                    "r_multiple": -0.95})
    assert "손절" in loss and "-1.90 USDT" in loss
    partial = TelegramNotifier.format({"event": "PARTIAL", "symbol": "ETH",
                                       "mode": "paper", "pnl_usd": 1.1,
                                       "r_multiple": 1.0, "exit_price": 3450,
                                       "qty": 0.5})
    assert "부분익절" in partial
    assert "익절수량" in partial and "0.5" in partial   # 청산한 수량 표시
    # 전량 청산(CLOSE)엔 qty 가 없어 익절수량 줄이 뜨지 않는다
    assert "익절수량" not in TelegramNotifier.format(_close_row("WIN"))
    print("ok  format CLOSE/PARTIAL (result label, signed pnl + R, 익절수량)")


# ------------------------------------------------------------- event filter

def test_event_filter():
    n = TelegramNotifier(token="t", chat_id="c", events="OPEN,CLOSE")
    captured: list[str] = []
    n._enqueue = lambda text: captured.append(text)   # bypass network + thread
    n.notify_trade(_open_row())
    n.notify_trade(_close_row())
    n.notify_trade({"event": "PARTIAL", "symbol": "ETH"})   # not in event set
    n.notify_trade({"event": "ADD", "symbol": "ETH"})       # not in event set
    assert len(captured) == 2, captured
    print("ok  event filter (only configured events push)")


def test_disabled_is_noop():
    n = TelegramNotifier(token="", chat_id="")
    sent: list[str] = []
    n._enqueue = lambda text: sent.append(text)
    n.notify_trade(_open_row())          # disabled -> must not enqueue
    n.send_text("hi")                    # disabled -> must not enqueue
    assert sent == []
    print("ok  disabled notifier is a silent no-op")


# ------------------------------------------------------------- delivery

def test_deliver_swallows_errors():
    def _boom(*a, **k):
        raise RuntimeError("network down")

    orig, notify.httpx.post = notify.httpx.post, _boom
    try:
        # must not raise even though the transport blows up
        TelegramNotifier(token="t", chat_id="c")._deliver("x")
    finally:
        notify.httpx.post = orig
    print("ok  _deliver swallows transport errors (trading never breaks)")


def test_worker_delivers_payload():
    calls: list[dict] = []

    def _capture(url, json=None, timeout=None):
        calls.append({"url": url, "json": json})

    orig, notify.httpx.post = notify.httpx.post, _capture
    try:
        n = TelegramNotifier(token="TOK", chat_id="99", events="OPEN")
        n.notify_trade(_open_row())
        n._q.join()                       # wait for the worker to drain
    finally:
        notify.httpx.post = orig
    assert len(calls) == 1
    assert "botTOK/sendMessage" in calls[0]["url"]
    assert calls[0]["json"]["chat_id"] == "99"
    assert "매수" in calls[0]["json"]["text"]
    print("ok  background worker posts the formatted payload to Telegram")


# ------------------------------------------------------------- journal sink

def test_journal_sink_receives_rows():
    seen: list[dict] = []
    j = Journal(sink=seen.append)
    j.append({"symbol": "ETH", "event": "OPEN", "side": "LONG"})
    assert seen and seen[0]["event"] == "OPEN" and seen[0]["symbol"] == "ETH"

    # a raising sink must never break the append path
    j2 = Journal(sink=lambda row: (_ for _ in ()).throw(ValueError("x")))
    j2.append({"symbol": "ETH", "event": "CLOSE"})   # should not raise
    print("ok  Journal sink fires per row and isolates sink failures")


# ------------------------------------------------------------- signal alert

class _Sig:                              # 최소 TradeSignal 스텁
    class _S:
        def __init__(self, v): self.value = v
    def __init__(self, side, detail, entry, stop):
        self.side = self._S(side)
        self.detail, self.entry_hint, self.stop_price = detail, entry, stop


def test_signal_alert_decoupled_from_account():
    """시그널 알림은 저널(체결)을 안 거치고 조건 충족 즉시 발송된다. 'SIGNAL'
    이벤트로 게이팅되고, 포맷에 '시그널'·트리거·기준가·손절가가 들어간다."""
    sig = _Sig("SHORT", "Donchian55 low 1842.1 broken @ 1823.21", 1823.21, 1861.36)
    # target + block reason surface; no "paper" ever leaks into the message
    msg = TelegramNotifier.format_signal(
        "ETH", "prop_breakout", "SHORT", sig.detail, sig.entry_hint,
        sig.stop_price, target=1746.91,
        blocked="prop budget guard: open risk $221 > 50% daily room",
        risk_pct=0.42, risk_usd=41.7)
    assert "시그널" in msg and "매도" in msg and "ETH" in msg
    assert "1823.21" in msg and "1861.36" in msg and "Donchian55" in msg
    assert "목표가" in msg and "1746.91" in msg          # target line
    assert "차단" in msg and "budget guard" in msg        # block reason
    assert "예상리스크" in msg and "0.42%" in msg and "41.7" in msg  # risk line
    assert "paper" not in msg.lower()                     # paper never exposed
    # not blocked → clean "조건 충족 신호" line, still no paper
    clean = TelegramNotifier.format_signal("ETH", "prop_breakout", "LONG",
                                           "d", 1.0, 0.9, target=1.2)
    assert "차단" not in clean and "조건 충족" in clean and "paper" not in clean.lower()
    assert "예상리스크" not in clean                       # risk omitted when absent

    # SIGNAL 이 이벤트에 있고 활성일 때만 enqueue
    sent: list[str] = []
    n = TelegramNotifier(token="t", chat_id="c", events="OPEN,CLOSE,SIGNAL")
    n._enqueue = lambda text: sent.append(text)
    n.notify_signal("ETH", "prop_breakout", sig)
    assert sent and "시그널" in sent[0]

    # SIGNAL 이 빠지면 무발송
    sent.clear()
    n2 = TelegramNotifier(token="t", chat_id="c", events="OPEN,CLOSE")
    n2._enqueue = lambda text: sent.append(text)
    n2.notify_signal("ETH", "prop_breakout", sig)
    assert sent == []
    # 비활성(토큰 없음)도 무발송
    n3 = TelegramNotifier(token="", chat_id="", events="SIGNAL")
    n3._enqueue = lambda text: sent.append(text)
    n3.notify_signal("ETH", "prop_breakout", sig)
    assert sent == []
    print("ok  signal alert (condition-met, SIGNAL-gated, account-independent)")


def test_signal_alert_dedup_in_symbolbot():
    """SymbolBot._signal_alert 는 에피소드 단위로 디둡 — 같은 방향 연속은 1회,
    시그널이 사라졌다 다시 뜨거나 방향이 바뀌면 재발송(전체 조립 없이 언바운드 호출)."""
    from app.trading.symbol_bot import SymbolBot

    class _N:
        def __init__(self): self.sigs = []
        def notify_signal(self, sym, strat, sig, **kw):
            self.sigs.append(sig.side.value)

    class _Bot:
        spec = type("S", (), {"key": "ETH"})()
        strategy_name = "prop_breakout"
        cfg = type("C", (), {"rr_target": 2.0})()
        signal_journal = None
        def __init__(self, n): self.notifier = n; self._last_signal_side = None
        def _signal_risk(self): return None, None
    _Bot._signal_alert = SymbolBot._signal_alert

    n = _N(); b = _Bot(n)
    lg, sh = _Sig("LONG", "d", 1, 0.9), _Sig("SHORT", "d", 1, 1.1)
    b._signal_alert(lg)      # None→LONG  발송
    b._signal_alert(lg)      # LONG→LONG  디둡
    b._signal_alert(None)    # LONG→None  리셋(무발송)
    b._signal_alert(lg)      # None→LONG  재발송
    b._signal_alert(sh)      # LONG→SHORT 발송
    assert n.sigs == ["LONG", "LONG", "SHORT"]
    print("ok  signal alert dedup (episode-based, re-fires on flip/clear)")


# ------------------------------------------------------------- breach alert

def test_breach_alert_fires_even_when_flat():
    """A prop breach must push a distinct high-priority alert regardless of open
    positions (a breach while flat emits no CLOSE rows), and a notifier failure
    must never block the kill-switch trip (breach handling is sacred)."""
    from app.trading.bot import TradingManager
    from app.trading.config import TradingConfig

    mgr = TradingManager(TradingConfig())
    sent: list[str] = []
    mgr.notifier.send_text = lambda text: sent.append(text)   # stub push
    mgr._on_prop_breach("daily loss -5% breached")
    assert sent and "브리치" in sent[0] and "daily loss" in sent[0]
    assert mgr.risk.kill_switch is True          # trip happened after the alert

    # a raising notifier must NOT stop the trip/flatten
    mgr2 = TradingManager(TradingConfig())
    mgr2.notifier.send_text = lambda text: (_ for _ in ()).throw(RuntimeError("x"))
    mgr2._on_prop_breach("max drawdown floor")   # must not raise
    assert mgr2.risk.kill_switch is True
    print("ok  breach alert fires when flat + never blocks the kill switch")


def main():
    test_enabled_gate()
    test_format_open()
    test_format_close()
    test_event_filter()
    test_disabled_is_noop()
    test_deliver_swallows_errors()
    test_worker_delivers_payload()
    test_journal_sink_receives_rows()
    test_signal_alert_decoupled_from_account()
    test_signal_alert_dedup_in_symbolbot()
    test_breach_alert_fires_even_when_flat()
    print("\nALL notify tests passed")


if __name__ == "__main__":
    main()
