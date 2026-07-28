"""트레이드 저널 청산 사유별 승패 분해 (Journal.by_reason) 오프라인 테스트."""
from app.trading import store
from app.trading.store import Journal, close_reason


def test_close_reason_extraction():
    """R-멀티플 꼬리에서 사유 범주 추출 — 리셋청산은 트레일링 괄호를 포함해도 정확."""
    assert close_reason("CLOSE @ 105 pnl +3.2 (1.6R) stop hit") == "stop"
    assert close_reason("x (-0.1R) time stop 24 bars") == "time_stop"
    assert close_reason("x (-0.6R) pre-reset flatten (00:30 UTC guard)") == "reset_flatten"
    assert close_reason("x (-1.0R) prop breach: daily loss") == "prop_breach"
    assert close_reason("x (0.0R) manual close") == "manual"
    assert close_reason("") == "other"
    print("ok  close_reason extraction (tail + bucket mapping)")


def test_by_reason_breakdown():
    """settled CLOSE 를 사유로 그룹핑 — 같은 'stop' 버킷 안에서 트레일 익절(WIN)과
    하드스탑(LOSS)이 갈리고, 최다 건수 버킷이 앞에 오며, 심볼 필터가 동작한다."""
    store.TRADES_CSV.unlink(missing_ok=True)   # isolate the breakdown assertion
    j = Journal()
    for sym, res, pnl, r, note in [
            ("BTC", "WIN", 3.20, 1.6, "x (1.6R) stop hit"),
            ("BTC", "LOSS", -5.00, -1.0, "x (-1.0R) stop hit"),
            # 부분익절 후 트레일 청산: result=CLOSED 지만 순수익 → 승으로 집계돼야 함
            ("BTC", "CLOSED", 2.10, 0.7, "x (0.7R) stop hit"),
            ("BTC", "LOSS", -0.50, -0.1, "x (-0.1R) time stop 24 bars"),
            ("ETH", "LOSS", -2.87, -0.6, "x (-0.6R) pre-reset flatten (00:30 UTC guard)")]:
        j.append({"symbol": sym, "event": "CLOSE", "result": res,
                  "pnl_usd": pnl, "r_multiple": r, "reason": note})
    br = j.by_reason()
    # 스탑은 손익으로 분리: 수익 청산(WIN 3.20 + CLOSED 2.10) → stop_trail,
    # 손실(-5.00) → stop(하드스탑). 최다 건수 버킷(stop_trail 2건)이 앞.
    assert list(br)[0] == "stop_trail", br
    assert br["stop_trail"]["trades"] == 2 and br["stop_trail"]["wins"] == 2
    assert round(br["stop_trail"]["pnl_usd"], 2) == 5.3
    assert br["stop"]["trades"] == 1 and br["stop"]["losses"] == 1
    assert br["time_stop"]["trades"] == 1 and br["reset_flatten"]["pnl_usd"] == -2.87
    eth = j.by_reason(symbol="ETH")            # 심볼 필터: ETH 는 리셋청산 1건만
    assert set(eth) == {"reset_flatten"} and eth["reset_flatten"]["trades"] == 1, eth
    store.TRADES_CSV.unlink(missing_ok=True)
    print("ok  journal by_reason (close-reason attribution, stop win/loss split)")


if __name__ == "__main__":
    test_close_reason_extraction()
    test_by_reason_breakdown()
    print("\nall journal-reason tests passed ✅")
