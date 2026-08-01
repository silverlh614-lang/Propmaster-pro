"""연구 전용 BehDark 시그널 사후판정기(judge_signal) 오프라인 테스트 — 네트워크 없음."""
import datetime as dt

from app.trading.research import SIGNALS, _ts, judge_signal


def _sig(side="LONG", **kw):
    """합성 시그널 — LONG: 존 [100,95], TP [110,120], SL 90 (risk=10)."""
    base = dict(symbol="TESTUSDT", side=side, ts=1000.0,
                entry=[100.0, 95.0], targets=[110.0, 120.0], stop=90.0)
    if side == "SHORT":
        base.update(entry=[100.0, 105.0], targets=[90.0, 80.0], stop=110.0)
    base.update(kw)
    return base


def _bar(ts_s, o, h, lo, c):
    return (ts_s * 1000.0, o, h, lo, c)


def test_ts_kst_conversion():
    """카드 시각은 KST → UTC epoch 로 변환 (KST = UTC+9), 시각 미상은 00:00."""
    expect = dt.datetime(2026, 6, 21, 10, 22, tzinfo=dt.timezone.utc).timestamp()
    assert _ts("2026-06-21", "19:22") == expect
    assert _ts("2026-07-21", None) == dt.datetime(
        2026, 7, 20, 15, 0, tzinfo=dt.timezone.utc).timestamp()
    print("ok  _ts KST->UTC conversion (incl. time-unknown midnight)")


def test_fill_tp1_then_be():
    """체결 → TP1 → 본전 스탑: 절반 +1R 실현, 나머지 0R → r_ladder 0.5."""
    out = judge_signal(_sig(), [
        _bar(2000, 101, 102, 99, 101),    # 존 근접변 100 터치 = 체결
        _bar(5600, 101, 111, 100.5, 108),  # TP1 110 도달 → 스탑 본전 이동
        _bar(9200, 108, 109, 99, 100)])    # 본전 100 터치 = BE 청산
    assert out["status"] == "TP1_THEN_BE" and out["tps_hit"] == 1, out
    assert out["r_ladder"] == 0.5 and out["days_to_fill"] == 0.0, out
    print("ok  fill -> TP1 -> breakeven exit (r_ladder 0.5)")


def test_no_fill_variants():
    """체결 전 TP1 선도달=RAN(방 관례상 취소), 체결 전 스탑=STOPPED, 데이터 끝=NO_FILL."""
    ran = judge_signal(_sig(), [_bar(2000, 106, 111, 105, 110)])
    stopped = judge_signal(_sig(), [_bar(2000, 94, 94.5, 85, 91)])
    never = judge_signal(_sig(), [_bar(2000, 104, 106, 103, 105)])
    assert ran["status"] == "NO_FILL_RAN" and ran["r_ladder"] is None, ran
    assert stopped["status"] == "NO_FILL_STOPPED", stopped
    assert never["status"] == "NO_FILL", never
    print("ok  NO_FILL variants (ran / stopped / never touched)")


def test_sl_after_fill():
    """체결 후 TP 없이 스탑 → SL, r_ladder −1.0 (전량 손절)."""
    out = judge_signal(_sig(), [
        _bar(2000, 101, 102, 99, 101),
        _bar(5600, 99, 100, 89, 92)])
    assert out["status"] == "SL" and out["r_ladder"] == -1.0, out
    print("ok  SL after fill (-1.0R)")


def test_all_tp_ladder_math():
    """전 타겟 도달: 균등 분할 래더 R = 0.5·(10/10) + 0.5·(20/10) = 1.5."""
    out = judge_signal(_sig(), [
        _bar(2000, 101, 102, 99, 101),
        _bar(5600, 101, 121, 100.5, 119)])   # 한 봉에 TP1+TP2 (스탑 미터치)
    assert out["status"] == "ALL_TP" and out["tps_hit"] == 2, out
    assert out["r_ladder"] == 1.5, out
    print("ok  ALL_TP equal-fraction ladder math (1.5R)")


def test_same_bar_sl_and_tp1_is_sl():
    """같은 봉에서 SL·TP1 동시 터치 → SL 우선 (보수 판정, 엔진 규칙과 동일)."""
    out = judge_signal(_sig(), [
        _bar(2000, 101, 102, 99, 101),
        _bar(5600, 100, 111, 89, 95)])
    assert out["status"] == "SL" and out["r_ladder"] == -1.0, out
    print("ok  same-bar SL+TP1 -> SL first (conservative)")


def test_short_symmetry():
    """SHORT 대칭: 존 [100,105] 체결 → TP1 90 → 본전 복귀 = 0.5R."""
    out = judge_signal(_sig("SHORT"), [
        _bar(2000, 99, 101, 98, 99),       # 근접변 100 터치 = 체결
        _bar(5600, 99, 99.5, 89, 91),      # TP1 90 도달
        _bar(9200, 91, 101, 90.5, 100)])   # 본전 100 터치 = BE
    assert out["status"] == "TP1_THEN_BE" and out["r_ladder"] == 0.5, out
    print("ok  SHORT symmetry (fill/TP/BE mirrored)")


def test_pre_signal_bars_ignored_and_open():
    """시그널 이전 봉은 무시(그 봉의 스탑 터치는 무효), 미결 진행형은 OPEN."""
    out = judge_signal(_sig(), [
        _bar(500, 95, 96, 85, 90),          # ts 1000 이전 — 스탑가 터치했지만 무시
        _bar(2000, 101, 102, 99, 101),      # 체결
        _bar(5600, 101, 105, 100.5, 104)])  # 아무 것도 미도달
    assert out["status"] == "OPEN" and out["r_ladder"] is None, out
    empty = judge_signal(_sig(), [_bar(500, 95, 96, 85, 90)])
    assert empty["status"] == "NO_DATA", empty
    print("ok  pre-signal bars ignored / OPEN / NO_DATA")


def test_dataset_sanity():
    """내장 데이터셋 정합성: 방향별 존·타겟·스탑 순서가 전부 올바른가."""
    assert len(SIGNALS) == 17
    for s in SIGNALS:
        near, far = s["entry"]
        if s["side"] == "LONG":
            assert far < near < s["targets"][0] and s["stop"] < far, s
            assert s["targets"] == sorted(s["targets"]), s
        else:
            assert far > near > s["targets"][0] and s["stop"] > far, s
            assert s["targets"] == sorted(s["targets"], reverse=True), s
    print("ok  embedded dataset sanity (17 signals, ordered zones/ladders)")


if __name__ == "__main__":
    test_ts_kst_conversion()
    test_fill_tp1_then_be()
    test_no_fill_variants()
    test_sl_after_fill()
    test_all_tp_ladder_math()
    test_same_bar_sl_and_tp1_is_sl()
    test_short_symmetry()
    test_pre_signal_bars_ignored_and_open()
    test_dataset_sanity()
    print("\nall research-judge tests passed ✅")
