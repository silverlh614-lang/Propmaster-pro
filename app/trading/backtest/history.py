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
# 월별 아카이브는 익월 초에야 게시된다 — 그 공백은 일별 아카이브로만 메울 수 있다
# (daily_fallback=True 를 명시한 호출자만 사용: 백테스트 기본 경로는 월별 그대로).
DAILY_BASE = "https://data.binance.vision/data/futures/um/daily/klines"

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


def day_list(month: str, today: dt.date) -> list[str]:
    """'YYYY-MM' 안의 FINISHED 일자들 'YYYY-MM-DD' (오늘은 제외 — 진행 중)."""
    y, m = (int(x) for x in month.split("-"))
    out, d = [], dt.date(y, m, 1)
    while d.month == m and d < today:
        out.append(d.isoformat())
        d += dt.timedelta(days=1)
    return out


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


def _fetch_daily(symbol: str, iv: str, month: str,
                 today: dt.date) -> list[Candle]:
    """월별 zip 이 아직 없는 달을 일별 zip 으로 메운다 (8병렬 — 한 달 31요청)."""
    from concurrent.futures import ThreadPoolExecutor

    def one(day: str) -> list[Candle]:
        cache = HISTORY_DIR / f"{symbol}-{iv}-{day}.zip"
        if cache.exists():
            return parse_zip(cache.read_bytes())
        data = _download(f"{DAILY_BASE}/{symbol}/{iv}/{symbol}-{iv}-{day}.zip")
        if data is None:
            return []                      # 그날 아카이브 없음 — 건너뜀
        cache.write_bytes(data)
        return parse_zip(data)

    out: list[Candle] = []
    with ThreadPoolExecutor(max_workers=8) as ex:
        for cs in ex.map(one, day_list(month, today)):
            out.extend(cs)
    return out


def fetch_history(symbol: str, interval_code: str, months: int,
                  today: dt.date | None = None,
                  daily_fallback: bool = False) -> list[Candle]:
    """Candles for the last `months` finished months, oldest→newest,
    deduped on open time. Downloads once, then serves from the cache.
    daily_fallback=True 면 월별 미게시 달을 일별 아카이브로 메운다."""
    iv = _VISION_IV.get(interval_code)
    if iv is None:
        raise ValueError(f"unsupported interval {interval_code}")
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    today = today or dt.date.today()
    bars: dict[int, Candle] = {}
    for m in month_list(months, today):
        cache = HISTORY_DIR / f"{symbol}-{iv}-{m}.zip"
        if cache.exists():
            data = cache.read_bytes()
        else:
            data = _download(f"{VISION_BASE}/{symbol}/{iv}/{symbol}-{iv}-{m}.zip")
            if data is None:               # month not published yet
                if daily_fallback:
                    for c in _fetch_daily(symbol, iv, m, today):
                        bars[c.ts_ms] = c
                continue
            cache.write_bytes(data)
        for c in parse_zip(data):
            bars[c.ts_ms] = c
    if daily_fallback:                     # 진행 중인 달의 끝난 날들까지 이어붙임
        for c in _fetch_daily(symbol, iv, today.strftime("%Y-%m"), today):
            bars[c.ts_ms] = c
    return [bars[k] for k in sorted(bars)]
