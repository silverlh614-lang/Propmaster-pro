"""헬스 워치독(HealthMonitor) 오프라인 테스트 — 피드 정체·루프 오류의 edge-트리거
알림과 회복, 표본 기준선 처리를 fake 봇·notifier 로 검증. 네트워크 없음."""
from app.trading.health import HealthMonitor


class _Bot:
    """status() 만 흉내내는 fake SymbolBot."""
    def __init__(self, polls=0, note="watching — no signal", source="binance"):
        self.polls, self.note, self.source = polls, note, source

    def status(self):
        return {"note": self.note,
                "collector": {"polls": self.polls, "source": self.source}}


class _Mgr:
    def __init__(self, bots):
        self.bots = bots


class _Notifier:
    enabled = True
    def __init__(self):
        self.sent = []
    def send_text(self, text):
        self.sent.append(text)


def test_feed_stall_alert_and_recovery():
    """최초 점검은 기준선만(알림 없음). 폴이 정체하면 이상 1회, 다시 오르면 회복 1회."""
    n = _Notifier()
    eth = _Bot(polls=100)
    h = HealthMonitor(_Mgr({"ETH": eth}), n, interval_sec=300)

    r0 = h.check()                       # 기준선 — delta None → 알림 없음
    assert r0["alerts"] == 0 and n.sent == []
    assert h.snapshot()["ETH"]["ok"] is True

    # 폴이 안 늘었다 → 피드 정체 이상 알림 1회
    r1 = h.check()
    assert r1["alerts"] == 1 and len(n.sent) == 1
    assert "이상" in n.sent[0] and "ETH" in n.sent[0]
    assert h.snapshot()["ETH"]["ok"] is False
    assert h.snapshot()["ETH"]["problems"] == ["feed_stalled"]

    # 여전히 정체 → 재알림 없음 (edge-트리거, 도배 금지)
    h.check()
    assert len(n.sent) == 1

    # 폴이 다시 증가 → 회복 알림 1회
    eth.polls = 240
    h.check()
    assert len(n.sent) == 2 and "회복" in n.sent[1]
    assert h.snapshot()["ETH"]["ok"] is True
    print("ok  health feed-stall alert + edge-trigger + recovery")


def test_loop_error_and_disabled_notifier():
    """결정 루프 오류 감지 + notifier 미설정 시 무음(스냅샷은 계속 갱신)."""
    # 루프 오류 note → loop_error 이상
    n = _Notifier()
    bot = _Bot(polls=10, note="loop error: KeyError: 'x'")
    h = HealthMonitor(_Mgr({"BTC": bot}), n, interval_sec=300)
    h.check()                            # 기준선(폴 delta None) 이지만 loop_error 는 즉시
    assert h.snapshot()["BTC"]["problems"] == ["loop_error"]
    assert len(n.sent) == 1 and "이상" in n.sent[0]

    # notifier 비활성: 알림은 무음이어도 스냅샷은 갱신되어 /status 로 노출된다
    off = _Notifier(); off.enabled = False
    h2 = HealthMonitor(_Mgr({"BTC": _Bot(polls=5, note="loop error: boom")}),
                       off, interval_sec=300)
    r = h2.check()
    assert r["alerts"] == 1 and off.sent == []          # 집계는 했으나 발송 무음
    assert h2.snapshot()["BTC"]["ok"] is False
    print("ok  health loop-error detect + silent when notifier disabled")


def test_disabled_monitor_start_noop():
    """health_alerts=False 면 start() 가 워치독을 띄우지 않는다 (task None)."""
    h = HealthMonitor(_Mgr({}), None, interval_sec=300, enabled=False)
    h.start()
    assert h._task is None
    print("ok  health monitor disabled → start() no-op")


if __name__ == "__main__":
    test_feed_stall_alert_and_recovery()
    test_loop_error_and_disabled_notifier()
    test_disabled_monitor_start_noop()
    print("\nall health tests passed ✅")
