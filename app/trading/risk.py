"""@responsibility 리스크 단일 관문 — 레버리지·리스크%·오픈리스크·일일 캡·킬스위치, allow_entry 유일 허가점

Risk gate for the trading package. Every new entry AND every pyramiding add
funnels through allow_entry(); nothing places size without passing it. It
enforces the prop non-negotiables: leverage never exceeds the
hard cap, per-trade risk stays inside the rule budgets, total open risk
(base + adds) is capped, plus daily loss cap, daily trade count, concurrent
position limit and a consecutive-error kill switch. Counters are per UTC day
and persist via BotState. Kill reset is an explicit operator action only.
"""
from __future__ import annotations

import datetime as dt

from .config import TradingConfig, SymbolSpec
from .store import BotState, Journal


def _today() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d")


def size_position(equity_usd: float, risk_pct: float, entry: float,
                  stop: float, spec: SymbolSpec, leverage_max: float
                  ) -> tuple[float, float, str]:
    """Fixed-fractional ATR sizing: risk_usd = equity *
    risk% ; qty = risk_usd / stop_distance. Rounds to the symbol step and
    clamps notional so leverage never exceeds the hard cap.

    Returns (qty, risk_usd_at_qty, reason). qty == 0 means "cannot size" and
    `reason` says why (dust stop, below min qty, or leverage-clamped to 0)."""
    stop_dist = abs(entry - stop)
    if stop_dist <= 0 or entry <= 0 or equity_usd <= 0:
        return 0.0, 0.0, "invalid entry/stop"
    risk_usd = equity_usd * risk_pct / 100.0
    qty = risk_usd / stop_dist

    # leverage cap: notional = qty*entry must be <= equity * leverage_max
    max_notional = equity_usd * leverage_max
    if qty * entry > max_notional:
        qty = max_notional / entry
    # round DOWN to the step so we never round risk up past the cap
    step = spec.qty_step
    qty = (int(qty / step)) * step if step > 0 else qty
    qty = round(qty, 8)
    if qty < spec.min_qty:
        return 0.0, 0.0, f"qty {qty} < min {spec.min_qty} (risk too small)"
    return qty, round(qty * stop_dist, 4), ""


class RiskManager:
    def __init__(self, cfg: TradingConfig, journal: Journal, state: BotState):
        self.cfg = cfg
        self.journal = journal
        self.state_store = state
        st = state.load()
        self.kill_switch: bool = st.get("kill_switch", False)
        self.kill_reason: str = st.get("kill_reason", "")
        self._errors = 0
        self._books: dict[str, object] = {}   # symbol key -> PositionManager
        self._prop = None                     # optional PropDesk gate

    def attach_prop(self, desk) -> None:
        """Attach the prop desk so the challenge account's rules join the
        single permission point — a FAILED account blocks every new entry."""
        self._prop = desk

    def prop_risk_budget(self) -> dict | None:
        """Remaining prop budgets ({daily_room, dd_room}) for budget-based
        sizing; None when no live prop account governs."""
        if self._prop is None:
            return None
        return self._prop.risk_budget()

    # ---------------------------------------------------- global exposure

    def register_book(self, key: str, book) -> None:
        """Register a symbol's PositionManager so the caps see EVERY symbol's
        exposure (GLOBAL across symbols). The same key replaces the previous
        book — a bot restart never double-counts."""
        self._books[key] = book

    def global_exposure(self) -> tuple[int, float]:
        """(open position count, open risk USD) summed across all registered
        symbols — the input allow_entry's concurrent/open-risk caps expect."""
        n = sum(b.open_positions for b in self._books.values())
        risk = sum(b.open_risk_usd for b in self._books.values())
        return n, risk

    # ------------------------------------------------------------- checks

    def today(self) -> dict:
        return self.journal.aggregate(day=_today())

    def allow_entry(self, open_positions: int, open_risk_usd: float,
                    new_risk_usd: float, is_add: bool = False,
                    equity_usd: float | None = None) -> tuple[bool, str]:
        """Single permission point. open_positions / open_risk_usd describe
        live exposure right now; new_risk_usd is the R this order would add.

        equity_usd is the CURRENT compounded equity: every % cap is measured
        against what the account is worth now, not the starting stake.
        Falls back to the config stake if absent."""
        if self.kill_switch:
            return False, f"kill_switch: {self.kill_reason}"
        if self._prop is not None:
            ok, why = self._prop.entries_allowed()
            if not ok:
                return False, f"prop: {why}"
        t = self.today()
        equity = equity_usd if equity_usd and equity_usd > 0 else self.cfg.equity_usd
        if t["trades"] >= self.cfg.max_trades_per_day:
            return False, f"max_trades_per_day ({self.cfg.max_trades_per_day}) reached"
        if t["losses"] >= self.cfg.daily_stop_after_losses:
            return False, (f"daily_stop_after_losses "
                           f"({self.cfg.daily_stop_after_losses}) — 오늘은 정지")
        budget = self.prop_risk_budget()
        if budget is not None:
            room_cap = budget["daily_room"] * self.cfg.daily_open_risk_frac
            if open_risk_usd + new_risk_usd > room_cap + 1e-9:
                return False, (f"prop budget guard: open risk "
                               f"${open_risk_usd + new_risk_usd:.2f} > "
                               f"{self.cfg.daily_open_risk_frac:.0%} of remaining "
                               f"daily room ${budget['daily_room']:.2f}")
        if t["pnl_usd"] <= -abs(equity * self.cfg.daily_loss_cap_pct / 100.0):
            return False, f"daily_loss_cap (-{self.cfg.daily_loss_cap_pct}%) hit"
        if not is_add and open_positions >= self.cfg.max_concurrent_positions:
            return False, (f"max_concurrent_positions "
                           f"({self.cfg.max_concurrent_positions}) reached")
        cap = equity * self.cfg.max_total_open_risk_pct / 100.0
        if open_risk_usd + new_risk_usd > cap + 1e-9:
            return False, (f"total_open_risk would be "
                           f"${open_risk_usd + new_risk_usd:.2f} > cap ${cap:.2f}")
        return True, ""

    # ------------------------------------------------------------- events

    def record_error(self, err: str) -> None:
        self._errors += 1
        if self._errors >= self.cfg.max_consecutive_errors:
            self.trip(f"{self._errors} consecutive errors; last: {err[:200]}")

    def record_ok(self) -> None:
        self._errors = 0

    def trip(self, reason: str) -> None:
        self.kill_switch = True
        self.kill_reason = reason
        self._persist()

    def reset_kill(self) -> None:
        self.kill_switch = False
        self.kill_reason = ""
        self._errors = 0
        self._persist()

    def _persist(self) -> None:
        st = self.state_store.load()
        st["kill_switch"] = self.kill_switch
        st["kill_reason"] = self.kill_reason
        self.state_store.save(st)

    def status(self) -> dict:
        return {"kill_switch": self.kill_switch, "kill_reason": self.kill_reason,
                "consecutive_errors": self._errors, "today": self.today()}
