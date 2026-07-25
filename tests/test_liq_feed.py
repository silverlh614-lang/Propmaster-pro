"""Offline tests for the display-only liquidation feed (app/trading/liq_feed.py).
No network — parsing, rolling buffer, hourly totals and the API view only.
Run:  python -m tests.test_liq_feed"""
from __future__ import annotations

import json
import time

from app.trading import liq_feed
from app.trading.liq_feed import LiquidationFeed, parse_events


def _obj(sym="BTCUSDT", side="SELL", qty="1.0", ap="50000", t=None):
    return {"e": "forceOrder", "o": {
        "s": sym, "S": side, "q": qty, "p": ap, "ap": ap,
        "T": int((t if t is not None else time.time()) * 1000)}}


def _raw(**kw):
    return json.dumps(_obj(**kw))


def test_parse_sides_and_notional():
    """SELL 체결 = 롱 강제청산, BUY = 숏. 노셔널은 ap×q, 최소 $5K 미만은 버림,
    깨진 페이로드는 빈 목록 (피드는 절대 안 죽는다)."""
    (e,) = parse_events(_raw(side="SELL", qty="2.0", ap="3000"))
    assert e["side"] == "LONG_LIQ"
    assert e["notional_usd"] == 6000.0 and e["symbol"] == "BTCUSDT"
    assert parse_events(_raw(side="BUY"))[0]["side"] == "SHORT_LIQ"
    assert parse_events(_raw(qty="0.01", ap="1000")) == []   # $10 — 먼지 컷
    assert parse_events("not json") == []
    assert parse_events(json.dumps({"o": {"s": "X"}})) == []  # 필드 누락
    print("ok  parse (side mapping, notional floor, malformed swallowed)")


def test_parse_payload_shapes():
    """실스트림 페이로드 변형 전부 수용: @arr 배열 / combined-stream data 래핑 /
    o 없는 항목 혼재 — 단일 객체만 받던 초기 버그(LIVE인데 0건)의 회귀 방지."""
    arr = json.dumps([_obj(sym="ETHUSDT"), _obj(sym="SUIUSDT", side="BUY"),
                      {"e": "other"}])
    evts = parse_events(arr)
    assert [e["symbol"] for e in evts] == ["ETHUSDT", "SUIUSDT"]
    wrapped = json.dumps({"stream": "!forceOrder@arr", "data": _obj(sym="SOLUSDT")})
    assert parse_events(wrapped)[0]["symbol"] == "SOLUSDT"
    wrapped_arr = json.dumps({"data": [_obj(sym="XRPUSDT")]})
    assert parse_events(wrapped_arr)[0]["symbol"] == "XRPUSDT"
    print("ok  payload shapes (array, combined-stream wrap, mixed items)")


def test_buffer_and_hour_totals():
    """버퍼는 상한 유지(오래된 것부터 밀림), 시간 집계는 1시간 창 안의
    롱/숏 노셔널만 합산한다."""
    f = LiquidationFeed()
    now = time.time()
    old = {"ts": now - 7200, "symbol": "ETHUSDT", "side": "LONG_LIQ",
           "price": 3000.0, "notional_usd": 99999.0}
    f._ingest(old)
    for i in range(130):                                    # maxlen 120 초과
        f._ingest({"ts": now - i, "symbol": "BTCUSDT",
                   "side": "LONG_LIQ" if i % 2 else "SHORT_LIQ",
                   "price": 50000.0, "notional_usd": 10000.0})
    assert len(f._events) == 120                            # 상한
    tot = f._hour_totals(now=now)
    assert tot["count"] == 120                              # old 는 밀려남
    assert tot["long_usd"] + tot["short_usd"] == 120 * 10000.0
    print("ok  rolling buffer cap + 1h window totals")


def test_status_view_offline():
    """네트워크 없이도 status() 는 완전한 뷰를 준다 — connected False,
    limit 이 행 수를 캡, 최신이 앞."""
    f = LiquidationFeed()
    now = time.time()
    for i in range(10):
        f._ingest({"ts": now + i, "symbol": "SUIUSDT", "side": "SHORT_LIQ",
                   "price": 1.0, "notional_usd": 5000.0 + i})
    s = f.status(limit=3)
    assert s["connected"] is False and s["hour"]["count"] == 10
    assert len(s["events"]) == 3
    assert s["events"][0]["notional_usd"] == 5009.0          # 최신 먼저
    assert s["min_notional_usd"] == 5000.0
    print("ok  status view (offline, limit, newest-first)")


def test_module_import_does_not_start_thread():
    """임포트/생성만으로는 스레드가 안 뜬다 — 첫 API 호출이 지연 기동한다
    (오프라인 테스트·CI 에서 네트워크 시도 자체가 없어야 함)."""
    assert liq_feed.FEED._thread is None
    print("ok  lazy start (no thread on import)")


def main():
    test_parse_sides_and_notional()
    test_parse_payload_shapes()
    test_buffer_and_hour_totals()
    test_status_view_offline()
    test_module_import_does_not_start_thread()
    print("\nALL liq_feed tests passed")


if __name__ == "__main__":
    main()
