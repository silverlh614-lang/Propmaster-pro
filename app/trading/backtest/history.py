"""@responsibility Binance vision 월별 kline 아카이브 — 다운로드·캐시·파싱으로 장기 백테스트 히스토리 공급

Long-history kline source. The public REST kline API caps at 1000 bars
(15m ≈ 10 days) — useless for judging a prop strategy. Binance publishes
the full USDT-M futures kline history as monthly CSV zips on
data.binance.vision (plain HTTPS, no API key, no rate limit that matters):

    {BASE}/{SYMBOL}/{iv}/{SYMBOL}-{iv}-{YYYY-MM}.zip

fetch_history() downloads the requested months once into DATA_DIR/history/
(zips are kept as-is; a re-run reads from disk), parses and merges them
into the engine's Candle list. Only FINISHED months are fetched — the
current month lives on the live REST path, not here. Timestamps are
normalized to ms (2025+ archives switched to microseconds). A 404 month
(pair not listed yet) is skipped, never fatal.
"""
from __future__ import annotations

import csv
import datetime as dt
import io
import os
import zipfile
from pathlib import Path

from ..models import Candle

VISION_BASE = "https://data.binance.vision/data/futures/um/monthly/klines"

# engine interval code -> vision folder name
_VISION_IV = {"1": "1m", "3": "3m", "5": "5m", "15": "15m", "30": "30m",
              "60": "1h", "120": "2h", "240": "4h", "360": "6h",
              "720": "12h", "D": "1d", "W": "1w"}

_ROOT = Path(__file__).resolve().parents[3]
HISTORY_DIR = Path(os.getenv("DATA_DIR", _ROOT / "data")) / "history"


def month_list(months: int, today: dt.date | None = None) -> list[str]:
    """The last `months` FINISHED months as 'YYYY-MM', oldest first
    (the running month is excluded — its archive doesn't exist yet)."""
    today = today or dt.date.today()
    first_of_cur = today.replace(day=1)
    out: list[str] = []
    cur = first_of_cur
    for _ in range(months):
        cur = (cur - dt.timedelta(days=1)).replace(day=1)
        out.append(cur.strftime("%Y-%m"))
    return out[::-1]


def _download(url: str) -> bytes | None:
    """GET one archive; None on 404 (month not published for this pair)."""
    import httpx    # lazy: keeps the module importable in offline tests
    r = httpx.get(url, timeout=60, follow_redirects=True)
    if r.status_code == 404:
        return None
    r.raise_for_status()
    return r.content


def parse_zip(data: bytes) -> list[Candle]:
    """One monthly zip -> candles. Columns: open_time, o, h, l, c, volume,
    close_time, ... Header row and µs timestamps are both normalized."""
    out: list[Candle] = []
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        name = z.namelist()[0]
        text = z.read(name).decode("utf-8")
    for row in csv.reader(io.StringIO(text)):
        if not row or not row[0].strip().isdigit():
            continue                       # header line or blank
        ts = int(row[0])
        if ts > 100_000_000_000_000:       # microseconds (2025+ archives)
            ts //= 1000
        out.append(Candle(ts_ms=ts, open=float(row[1]), high=float(row[2]),
                          low=float(row[3]), close=float(row[4]),
                          volume=float(row[5])))
    return out


def fetch_history(symbol: str, interval_code: str, months: int,
                  today: dt.date | None = None) -> list[Candle]:
    """Candles for the last `months` finished months, oldest→newest,
    deduped on open time. Downloads once, then serves from the cache."""
    iv = _VISION_IV.get(interval_code)
    if iv is None:
        raise ValueError(f"unsupported interval {interval_code}")
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    bars: dict[int, Candle] = {}
    for m in month_list(months, today):
        cache = HISTORY_DIR / f"{symbol}-{iv}-{m}.zip"
        if cache.exists():
            data = cache.read_bytes()
        else:
            data = _download(f"{VISION_BASE}/{symbol}/{iv}/{symbol}-{iv}-{m}.zip")
            if data is None:
                continue                   # month not published — skip
            cache.write_bytes(data)
        for c in parse_zip(data):
            bars[c.ts_ms] = c
    return [bars[k] for k in sorted(bars)]
