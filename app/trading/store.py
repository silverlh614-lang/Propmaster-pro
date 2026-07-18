"""@responsibility 트레이드 저널(CSV)·봇 상태 영속화 — 포지션 이벤트 기록과 재시작 생존 단일 통로

Trade journal (CSV) and bot-state persistence for the trading package. One row
per position lifecycle event (OPEN / ADD / PARTIAL / CLOSE). Aggregates read
the settled rows (WIN/LOSS/CLOSED) for PnL and count OPEN rows for the daily
trade cap. CSV so results survive restarts and export cleanly to Excel.
"""
from __future__ import annotations

import csv
import datetime as dt
import json
import os
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = Path(os.getenv("DATA_DIR", ROOT / "data"))
TRADES_CSV = DATA_DIR / "trades.csv"
SIGNALS_CSV = DATA_DIR / "signals.csv"
STATE_JSON = DATA_DIR / "engine_state.json"
POSITIONS_JSON = DATA_DIR / "positions.json"
ACCOUNT_JSON = DATA_DIR / "account.json"

FIELDS = [
    "ts", "symbol", "mode", "strategy", "event", "side", "signal_type",
    "signal_detail", "entry_price", "exit_price", "qty", "leverage",
    "notional_usd", "risk_usd", "result", "pnl_usd", "r_multiple",
    "fee_usd", "reason",
]

# event vocabulary
OPEN_EVENTS = ("OPEN",)                        # a new position started
SETTLED_RESULTS = ("WIN", "LOSS", "CLOSED")    # a position (or leg) realized PnL

# 시그널 저널 — 전략이 낸 모든 진입 신호(체결과 무관). 라이브 시그널이 조회 가능한
# 기록으로 남는다: 실제 진입됐는지(entered)·막혔으면 사유(blocked)까지.
SIGNAL_FIELDS = [
    "ts", "symbol", "strategy", "side", "signal_type",
    "entry", "target", "stop", "detail", "blocked", "entered",
    # 전진(forward) 추적 — 시그널이 목표/손절 중 뭘 먼저 쳤는지 (체결 무관)
    "outcome", "r_result", "bars_held", "resolved_ts",
]
SIGNAL_OPEN = ("", "OPEN")                      # 미결(추적 중)
SIGNAL_RESULTS = ("WIN", "LOSS")               # 결과가 확정된 시그널

_lock = threading.Lock()


def _utcnow() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def _epoch(iso: str) -> float | None:
    try:
        return dt.datetime.fromisoformat(iso).timestamp()
    except (TypeError, ValueError):
        return None


def _iso(epoch: float) -> str:
    return dt.datetime.fromtimestamp(epoch, dt.timezone.utc).isoformat(
        timespec="seconds")


class Journal:
    """CSV-backed journal, mtime-cached so status polling doesn't reparse."""

    def __init__(self, sink=None):
        self._cache_key: tuple | None = None
        self._cache_rows: list[dict] = []
        # optional one-way notifier (e.g. TelegramNotifier.notify_trade). Kept
        # generic so store.py owns no network dependency; must never raise.
        self._sink = sink

    def append(self, rec: dict) -> dict:
        row = {k: rec.get(k, "") for k in FIELDS}
        row["ts"] = row["ts"] or _utcnow()
        with _lock:
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            new = not TRADES_CSV.exists()
            with TRADES_CSV.open("a", newline="", encoding="utf-8") as f:
                w = csv.DictWriter(f, fieldnames=FIELDS)
                if new:
                    w.writeheader()
                w.writerow(row)
        if self._sink is not None:
            try:
                # 정규화된 CSV 필드(row) + CSV 밖 표시용 여분 키(rec: target/stop 등)
                self._sink({**rec, **row})
            except Exception:
                pass            # notification is a side channel — never block the journal
        return row

    def _rows(self) -> list[dict]:
        if not TRADES_CSV.exists():
            return []
        with _lock:
            st = TRADES_CSV.stat()
            key = (st.st_mtime_ns, st.st_size)
            if key != self._cache_key:
                with TRADES_CSV.open(newline="", encoding="utf-8") as f:
                    self._cache_rows = list(csv.DictReader(f))
                self._cache_key = key
            return self._cache_rows

    def tail(self, n: int = 50, symbol: str | None = None) -> list[dict]:
        rows = self._rows()
        if symbol:
            rows = [r for r in rows if r["symbol"] == symbol.upper()]
        return rows[-n:][::-1]

    def aggregate(self, day: str | None = None,
                  symbol: str | None = None) -> dict:
        """Overall (or per-UTC-day / per-symbol) stats. trades = OPEN events;
        settled = realized legs; pnl = sum over settled."""
        rows = self._rows()
        if day:
            rows = [r for r in rows if r["ts"][:10] == day]
        if symbol:
            rows = [r for r in rows if r["symbol"] == symbol.upper()]
        settled = [r for r in rows if r["result"] in SETTLED_RESULTS]
        wins = sum(1 for r in settled if r["result"] == "WIN")
        pnl = sum(float(r["pnl_usd"] or 0) for r in settled)
        fees = sum(float(r["fee_usd"] or 0) for r in rows)
        rs = [float(r["r_multiple"]) for r in settled if r["r_multiple"] not in ("", None)]
        return {
            "records": len(rows),
            "trades": sum(1 for r in rows if r["event"] in OPEN_EVENTS),
            "settled": len(settled),
            "wins": wins,
            "losses": len(settled) - wins,
            "win_rate": round(wins / len(settled), 4) if settled else None,
            "pnl_usd": round(pnl, 4),
            "fees_usd": round(fees, 4),
            "avg_r": round(sum(rs) / len(rs), 3) if rs else None,
            "expectancy_r": round(sum(rs) / len(rs), 3) if rs else None,
        }

    def by_symbol(self, symbols: list[str]) -> dict:
        return {s: self.aggregate(symbol=s) for s in symbols}


class SignalJournal:
    """CSV-backed record of every strategy signal (decoupled from execution).
    One row per emitted entry signal — the live signal history the telegram
    push also fires. mtime-cached like the trade journal so polling is cheap."""

    def __init__(self):
        self._cache_key: tuple | None = None
        self._cache_rows: list[dict] = []

    def append(self, rec: dict) -> dict:
        row = {k: rec.get(k, "") for k in SIGNAL_FIELDS}
        row["ts"] = row["ts"] or _utcnow()
        # 목표·손절이 있으면 전진 추적 대상 (OPEN) — 체결 여부와 무관
        if row["target"] not in ("", None) and row["stop"] not in ("", None):
            row["outcome"] = "OPEN"
        with _lock:
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            new = not SIGNALS_CSV.exists()
            with SIGNALS_CSV.open("a", newline="", encoding="utf-8") as f:
                w = csv.DictWriter(f, fieldnames=SIGNAL_FIELDS)
                if new:
                    w.writeheader()
                w.writerow(row)
        return row

    def _write_all(self, rows: list[dict]) -> None:
        with _lock:
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            with SIGNALS_CSV.open("w", newline="", encoding="utf-8") as f:
                w = csv.DictWriter(f, fieldnames=SIGNAL_FIELDS)
                w.writeheader()
                w.writerows({k: r.get(k, "") for k in SIGNAL_FIELDS} for r in rows)

    @staticmethod
    def _eval(r: dict, high: float, low: float, now_ts: float,
              bar_seconds: float, timeout_bars: int) -> dict | None:
        """이 봉의 high/low 로 미결 시그널을 판정. 목표=+rr R, 손절=−1R (한 봉이
        둘 다 스치면 손절 우선 — FSM 과 동일한 보수적 규칙). 타임아웃 시 EXPIRED."""
        try:
            entry, target, stop = float(r["entry"]), float(r["target"]), float(r["stop"])
        except (TypeError, ValueError, KeyError):
            return None
        risk = abs(entry - stop)
        if risk <= 0:
            return None
        long = str(r.get("side")).upper() == "LONG"
        loss = (low <= stop) if long else (high >= stop)
        win = (high >= target) if long else (low <= target)
        outcome, rr = None, None
        if loss:                                    # 보수적: 동시 터치면 손절 먼저
            outcome, rr = "LOSS", -1.0
        elif win:
            outcome = "WIN"
            rr = round((target - entry) / risk if long
                       else (entry - target) / risk, 3)
        else:
            emitted = _epoch(r.get("ts"))
            if emitted and (now_ts - emitted) > timeout_bars * bar_seconds:
                outcome, rr = "EXPIRED", 0.0
        if outcome is None:
            return None
        emitted = _epoch(r.get("ts"))
        bars = round((now_ts - emitted) / bar_seconds) if emitted else ""
        return {"outcome": outcome, "r_result": rr, "bars_held": bars,
                "resolved_ts": _iso(now_ts)}

    def resolve_open(self, symbol: str, high: float, low: float, now_ts: float,
                     bar_seconds: float, timeout_bars: int) -> int:
        """한 종목의 미결 시그널을 이 봉에 대해 판정하고, 결과가 확정된 것만
        CSV 를 다시 써 반영한다. 결과 없으면 디스크 미변경. 결정된 개수를 반환."""
        rows = [dict(r) for r in self._rows()]
        sym = symbol.upper()
        resolved = 0
        for r in rows:
            if r.get("symbol") == sym and r.get("outcome") in SIGNAL_OPEN \
                    and r.get("target") not in ("", None):
                res = self._eval(r, high, low, now_ts, bar_seconds, timeout_bars)
                if res:
                    r.update(res)
                    resolved += 1
        if resolved:
            self._write_all(rows)
        return resolved

    def _rows(self) -> list[dict]:
        if not SIGNALS_CSV.exists():
            return []
        with _lock:
            st = SIGNALS_CSV.stat()
            key = (st.st_mtime_ns, st.st_size)
            if key != self._cache_key:
                with SIGNALS_CSV.open(newline="", encoding="utf-8") as f:
                    self._cache_rows = list(csv.DictReader(f))
                self._cache_key = key
            return self._cache_rows

    def tail(self, n: int = 50, symbol: str | None = None) -> list[dict]:
        rows = self._rows()
        if symbol:
            rows = [r for r in rows if r["symbol"] == symbol.upper()]
        return rows[-n:][::-1]

    def stats(self, symbol: str | None = None) -> dict:
        """Signal history summary. Two lenses: (1) signal→execution gap (entered
        vs blocked by the risk gate) and (2) FORWARD performance — of the signals
        that resolved (hit target/stop), the live win-rate and R expectancy, a
        real-time validation of the backtested edge, independent of execution."""
        rows = self._rows()
        if symbol:
            rows = [r for r in rows if r.get("symbol") == symbol.upper()]
        entered = sum(1 for r in rows if str(r.get("entered")).lower() == "true")
        blocked = sum(1 for r in rows if r.get("blocked"))
        settled = [r for r in rows if r.get("outcome") in SIGNAL_RESULTS]
        wins = sum(1 for r in settled if r["outcome"] == "WIN")
        rs = [float(r["r_result"]) for r in settled
              if r.get("r_result") not in ("", None)]
        return {
            "records": len(rows), "entered": entered, "blocked": blocked,
            "open": sum(1 for r in rows if r.get("outcome") in SIGNAL_OPEN),
            "expired": sum(1 for r in rows if r.get("outcome") == "EXPIRED"),
            "resolved": len(settled), "wins": wins, "losses": len(settled) - wins,
            "win_rate": round(wins / len(settled), 4) if settled else None,
            "expectancy_r": round(sum(rs) / len(rs), 3) if rs else None,
        }


class BotState:
    """Small JSON blob: mode, kill switch, running flag — survives restarts."""

    def load(self) -> dict:
        if STATE_JSON.exists():
            try:
                return json.loads(STATE_JSON.read_text(encoding="utf-8"))
            except Exception:
                pass
        return {}

    def save(self, state: dict) -> None:
        with _lock:
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            STATE_JSON.write_text(json.dumps(state, ensure_ascii=False, indent=2),
                                  encoding="utf-8")


class AccountStore:
    """Single JSON record for the unified account (shared equity across all
    symbols — 한 계좌). Written through by AccountLedger on every change."""

    def load(self) -> dict:
        if ACCOUNT_JSON.exists():
            try:
                return json.loads(ACCOUNT_JSON.read_text(encoding="utf-8"))
            except Exception:
                pass
        return {}

    def save(self, rec: dict) -> None:
        with _lock:
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            ACCOUNT_JSON.write_text(json.dumps(rec, ensure_ascii=False, indent=2),
                                    encoding="utf-8")


class PositionStore:
    """Per-symbol PositionManager snapshots (the live open position) so an
    in-flight trade survives restarts and redeploys. Equity lives in
    AccountStore (통합 계좌); a legacy `equity` field in old records is only
    read once, for migration. Requires DATA_DIR on a persistent volume."""

    def _read(self) -> dict:
        if POSITIONS_JSON.exists():
            try:
                return json.loads(POSITIONS_JSON.read_text(encoding="utf-8"))
            except Exception:
                pass
        return {}

    def load(self, symbol: str) -> dict:
        return self._read().get(symbol.upper(), {})

    def save(self, symbol: str, state: dict) -> None:
        with _lock:
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            allst = self._read()
            allst[symbol.upper()] = state
            POSITIONS_JSON.write_text(
                json.dumps(allst, ensure_ascii=False, indent=2), encoding="utf-8")
