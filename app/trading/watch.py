"""@responsibility 실시간 근접 알림 — 매 폴링 현재가가 목표가·손절가에 근접하면 1회 텔레그램 푸시 (감시 전용)

Real-time proximity heads-up. Called each poll from SymbolBot for the symbol
holding an open paper position: it compares the live last_price (the single
KlineCollector price path, invariant #4) against the position's target and
stop, and fires ONE Telegram alert per level per position when price enters
the "near" band. A pure watcher — never trades, never touches the risk gate,
never raises into the loop.

Band width = TELEGRAM_NEAR_FRAC (default 0.15 of the entry->level distance);
set 0 to disable proximity alerts.
"""
from __future__ import annotations

import os

from .notify import _SEP, _SIDE_LABEL, _px


def _read_frac() -> float:
    try:
        return max(0.0, float(os.getenv("TELEGRAM_NEAR_FRAC", "0.15")))
    except ValueError:
        return 0.15


NEAR_FRAC = _read_frac()


def _near(price: float, entry: float, level: float, frac: float) -> bool:
    """True when price has covered >= (1-frac) of the entry->level move —
    i.e. it is within `frac` of the distance from the level."""
    dist = abs(level - entry)
    if dist <= 0 or frac <= 0:
        return False
    return abs(level - price) <= dist * frac


def _msg(sym: str, side: str, price: float, label: str, level: float,
         level_name: str, entry: float | None = None) -> str:
    """근접 알림 한 통. level_name 은 그 값이 실제로 무엇인지(손절가/목표가)를
    라벨로 박는다 — 예전엔 손절/목표 값을 뭉뚱그려 '기준가'로 표기해 혼란을 줬다.
    진입가도 함께 실어 현재가가 진입가↔레벨 사이 어디인지 한눈에 보이게 한다."""
    lines = [f"{label}  {sym}", _SEP]
    if entry not in (None, ""):
        lines.append(f"진입가   {_px(entry)}")
    lines += [f"현재가   {_px(price)}",
              f"{level_name}   {_px(level)}",
              f"방향     {_SIDE_LABEL.get(side.upper(), side)}"]
    return "\n".join(lines)


def proximity_scan(bot) -> None:
    """One per-poll proximity check for a SymbolBot. Fires at most once per
    level (target/stop) per open position; the fired-set resets when the bot
    goes flat. Never raises — notification is a side channel."""
    try:
        notifier = getattr(bot, "notifier", None)
        if notifier is None or not notifier.enabled or NEAR_FRAC <= 0:
            return
        pm = bot.pm
        pos = pm.pos if pm else None
        if not pos or pos.state.value != "OPEN":
            bot._prox_fired = set()      # new position starts with a clean slate
            return
        price = bot.collector.last_price()
        if price is None:
            return
        fired = getattr(bot, "_prox_fired", None)
        if fired is None:
            fired = bot._prox_fired = set()
        entry, side = pos.avg_entry, pos.side.value
        target, stop = pos.target_price, pos.stop_price
        if (target is not None and "target" not in fired
                and _near(price, entry, target, NEAR_FRAC)):
            fired.add("target")
            notifier.send_text(_msg(bot.spec.key, side, price,
                                    "🎯 목표가 근접", target, "목표가", entry))
        if (stop is not None and "stop" not in fired
                and _near(price, entry, stop, NEAR_FRAC)):
            fired.add("stop")
            notifier.send_text(_msg(bot.spec.key, side, price,
                                    "⚠️ 손절가 근접", stop, "손절가", entry))
    except Exception:
        pass
