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
                                       "r_multiple": 1.0, "exit_price": 3450})
    assert "부분익절" in partial
    print("ok  format CLOSE/PARTIAL (result label, signed pnl + R)")


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


def main():
    test_enabled_gate()
    test_format_open()
    test_format_close()
    test_event_filter()
    test_disabled_is_noop()
    test_deliver_swallows_errors()
    test_worker_delivers_payload()
    test_journal_sink_receives_rows()
    print("\nALL notify tests passed")


if __name__ == "__main__":
    main()
