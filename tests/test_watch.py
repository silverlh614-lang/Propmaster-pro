"""Offline tests for the real-time proximity monitor (app/trading/watch.py).
No network, no engine — a fake bot drives proximity_scan directly. Run:
python -m tests.test_watch"""
from __future__ import annotations

from types import SimpleNamespace

from app.trading import watch
from app.trading.watch import proximity_scan

watch.NEAR_FRAC = 0.15          # pin band width (default; ignore any env)


class FakeNotifier:
    def __init__(self, enabled=True):
        self.enabled = enabled
        self.sent: list[str] = []

    def send_text(self, text):
        self.sent.append(text)


def _pos(side, entry, target, stop, state="OPEN"):
    return SimpleNamespace(state=SimpleNamespace(value=state),
                           side=SimpleNamespace(value=side),
                           avg_entry=entry, target_price=target, stop_price=stop)


def _bot(notifier, pos, price):
    return SimpleNamespace(notifier=notifier,
                           pm=SimpleNamespace(pos=pos),
                           collector=SimpleNamespace(last_price=lambda: price),
                           spec=SimpleNamespace(key="ETH"))


# ------------------------------------------------------------- band math

def test_near_band():
    # LONG entry 100 -> target 110 (dist 10), band = within 1.5 of the level
    assert watch._near(109.0, 100.0, 110.0, 0.15) is True     # remaining 1.0
    assert watch._near(105.0, 100.0, 110.0, 0.15) is False    # remaining 5.0
    assert watch._near(100.0, 100.0, 100.0, 0.15) is False    # zero distance
    assert watch._near(109.0, 100.0, 110.0, 0.0) is False     # disabled
    print("ok  near-band math (covered >= 1-frac of the move)")


# ------------------------------------------------------------- firing

def test_target_proximity_fires_once():
    n = FakeNotifier()
    bot = _bot(n, _pos("LONG", 100, 110, 95), 109)   # near target, far from stop
    proximity_scan(bot)
    assert len(n.sent) == 1 and "목표가 근접" in n.sent[0]
    assert "ETH" in n.sent[0] and "paper" not in n.sent[0]
    # 레벨 값은 실제 이름(목표가)으로 라벨링 — 뭉뚱그린 '기준가' 금지
    assert "목표가   110" in n.sent[0] and "기준가" not in n.sent[0]
    assert "진입가   100" in n.sent[0]                # 진입가 맥락 포함
    proximity_scan(bot)                               # one-shot: no repeat
    assert len(n.sent) == 1
    print("ok  target proximity fires exactly once per position")


def test_stop_proximity_fires():
    n = FakeNotifier()
    bot = _bot(n, _pos("LONG", 100, 130, 95), 95.5)  # near stop, far from target
    proximity_scan(bot)
    assert len(n.sent) == 1 and "손절가 근접" in n.sent[0]
    # 스탑 값은 '손절가'로 라벨 — 예전 '기준가' 오표기가 혼란의 원인이었다
    assert "손절가   95" in n.sent[0] and "기준가" not in n.sent[0]
    print("ok  stop proximity fires when price nears the stop (labelled 손절가)")


def test_short_side_label():
    n = FakeNotifier()
    bot = _bot(n, _pos("SHORT", 100, 90, 105), 91)   # short toward target
    proximity_scan(bot)
    assert len(n.sent) == 1 and "매도" in n.sent[0]
    print("ok  short position renders the 매도 direction")


def test_no_fire_when_far():
    n = FakeNotifier()
    proximity_scan(_bot(n, _pos("LONG", 100, 110, 90), 103))
    assert n.sent == []
    print("ok  no alert while price is outside both bands")


def test_reset_on_flat():
    n = FakeNotifier()
    bot = _bot(n, _pos("LONG", 100, 110, 95), 109)
    proximity_scan(bot)
    assert len(n.sent) == 1
    bot.pm.pos = _pos("LONG", 100, 110, 95, state="CLOSED")   # went flat
    proximity_scan(bot)
    assert len(n.sent) == 1
    # a fresh position near target should alert again
    bot.pm.pos = _pos("LONG", 100, 110, 95)
    proximity_scan(bot)
    assert len(n.sent) == 2
    print("ok  fired-set resets when flat (new position alerts again)")


def test_disabled_notifier_no_send():
    n = FakeNotifier(enabled=False)
    proximity_scan(_bot(n, _pos("LONG", 100, 110, 95), 109))
    assert n.sent == []
    print("ok  disabled notifier = silent (never sends)")


def main():
    test_near_band()
    test_target_proximity_fires_once()
    test_stop_proximity_fires()
    test_short_side_label()
    test_no_fire_when_far()
    test_reset_on_flat()
    test_disabled_notifier_no_send()
    print("\nALL watch tests passed")


if __name__ == "__main__":
    main()
