"""Shared helpers: config loading, Binance klines fetch, JSONL signal queue."""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen

REPO_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = REPO_ROOT / "config.json"
CONFIG_EXAMPLE = REPO_ROOT / "config.example.json"
BRIDGE_DIR = REPO_ROOT / "bridge"
QUEUE_PATH = BRIDGE_DIR / "signal_queue.jsonl"
PLANS_DIR = BRIDGE_DIR / "plans"


def load_config() -> dict[str, Any]:
    path = CONFIG_PATH if CONFIG_PATH.exists() else CONFIG_EXAMPLE
    with open(path) as f:
        return json.load(f)


def write_signal(signal: dict[str, Any]) -> None:
    BRIDGE_DIR.mkdir(parents=True, exist_ok=True)
    signal.setdefault("ts", iso_now())
    with open(QUEUE_PATH, "a") as f:
        f.write(json.dumps(signal, separators=(",", ":")) + "\n")


def iso_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def http_get(url: str, params: dict | None = None, timeout: int = 15) -> Any:
    if params:
        url = f"{url}?{urlencode(params)}"
    req = Request(url, headers={"User-Agent": "claude-crypto-trader/0.1"})
    with urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


# --- Binance public REST with geoblock-aware fallback ---
# api.binance.com returns 451/403 from US-based IPs (incl. GitHub Actions
# US-East runners). We try the regional mirrors first; if all are blocked
# we fall back to Binance.US which has fewer pairs but works from the US.

_BINANCE_HOSTS = [
    "https://api-gcp.binance.com",   # Google Cloud mirror; usually unblocked
    "https://api1.binance.com",
    "https://api2.binance.com",
    "https://api3.binance.com",
    "https://api4.binance.com",
    "https://api.binance.com",
]
_BINANCE_US = "https://api.binance.us"
_BINANCE_HOST_CACHE: dict[str, str] = {}  # path → working host for this run


def _binance_call(path: str, params: dict | None = None) -> Any:
    """Try Binance mirrors; on geoblock (451/403) fall back to Binance.US."""
    import sys as _sys
    import urllib.error
    cached = _BINANCE_HOST_CACHE.get(path)
    hosts = [cached] if cached else list(_BINANCE_HOSTS)
    last_err: Exception | None = None
    for host in hosts:
        try:
            data = http_get(f"{host}{path}", params)
            if host not in _BINANCE_HOST_CACHE.values():
                print(f"[binance] using {host} for {path}", file=_sys.stderr)
            _BINANCE_HOST_CACHE[path] = host
            return data
        except urllib.error.HTTPError as e:
            last_err = e
            if e.code in (451, 403, 418, 429):
                continue
            raise
        except Exception as e:
            last_err = e
            continue
    # Final fallback: Binance.US (works from US; limited pair universe).
    # NOTE: binance.us uses the same /api/v3 paths but returns a smaller
    # ticker list, and some USDT pairs don't exist there (uses USD instead).
    try:
        data = http_get(f"{_BINANCE_US}{path}", params)
        print(f"[binance] FALLBACK to Binance.US for {path} (reduced pair universe)", file=_sys.stderr)
        _BINANCE_HOST_CACHE[path] = _BINANCE_US
        return data
    except Exception as e:
        raise RuntimeError(
            f"All Binance mirrors blocked (last com-error: {last_err}; us-error: {e}). "
            f"GitHub Actions runs in US — api.binance.com returns 451 there. "
            f"Switch to a self-hosted runner or use Bybit as data source."
        )


def binance_klines(symbol: str, interval: str = "15m", limit: int = 100) -> list[list]:
    """Return raw klines: [open_time, o, h, l, c, v, close_time, qv, trades, tbbv, tbqv, _]"""
    return _binance_call("/api/v3/klines", {"symbol": symbol, "interval": interval, "limit": limit})


def binance_top_pairs(quote: str = "USDT", top_n: int = 50, min_vol_usd: float = 5e7) -> list[str]:
    """Return top N pairs by 24h quote volume, filtered by min volume.

    If the Binance.US fallback is in use (fewer USDT pairs), also fall back
    to USD-quoted pairs so the scanner still has symbols to work with.
    """
    import sys as _sys
    tickers = _binance_call("/api/v3/ticker/24hr")
    if not isinstance(tickers, list):
        print(f"[binance] unexpected ticker response type: {type(tickers)}", file=_sys.stderr)
        return []

    def pick(q: str) -> list[dict]:
        return [
            t for t in tickers
            if t.get("symbol", "").endswith(q)
            and float(t.get("quoteVolume", 0) or 0) >= min_vol_usd
            and not any(x in t.get("symbol", "") for x in ("UP", "DOWN", "BULL", "BEAR"))
        ]

    rows = pick(quote)
    if not rows and quote == "USDT":
        # Binance.US fallback: try USD pairs with a lower threshold
        rows = [
            t for t in tickers
            if t.get("symbol", "").endswith("USD")
            and float(t.get("quoteVolume", 0) or 0) >= min_vol_usd / 10
        ]
        if rows:
            print(f"[binance] no USDT pairs matched; fell back to {len(rows)} USD pairs", file=_sys.stderr)

    rows.sort(key=lambda t: float(t.get("quoteVolume", 0) or 0), reverse=True)
    return [t["symbol"] for t in rows[:top_n]]


# --- Simple TA helpers (no external deps) ---

def atr(highs: list[float], lows: list[float], closes: list[float], period: int = 14) -> float:
    """Average True Range."""
    trs = []
    for i in range(1, len(closes)):
        tr = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        )
        trs.append(tr)
    if len(trs) < period:
        return sum(trs) / max(len(trs), 1)
    return sum(trs[-period:]) / period


def zscore(values: list[float], window: int = 20) -> float:
    """Z-score of the last value vs the preceding `window`."""
    if len(values) < window + 1:
        return 0.0
    sample = values[-window - 1 : -1]
    mean = sum(sample) / len(sample)
    var = sum((v - mean) ** 2 for v in sample) / len(sample)
    sd = var ** 0.5
    if sd == 0:
        return 0.0
    return (values[-1] - mean) / sd


def rsi(closes: list[float], period: int = 14) -> float:
    gains, losses = [], []
    for i in range(1, len(closes)):
        d = closes[i] - closes[i - 1]
        gains.append(max(d, 0))
        losses.append(max(-d, 0))
    if len(gains) < period:
        return 50.0
    avg_g = sum(gains[-period:]) / period
    avg_l = sum(losses[-period:]) / period
    if avg_l == 0:
        return 100.0
    rs = avg_g / avg_l
    return 100 - (100 / (1 + rs))


@dataclass
class Candle:
    open_time: int
    o: float
    h: float
    l: float
    c: float
    v: float

    @classmethod
    def from_binance(cls, row: list) -> "Candle":
        return cls(
            open_time=int(row[0]),
            o=float(row[1]),
            h=float(row[2]),
            l=float(row[3]),
            c=float(row[4]),
            v=float(row[5]),
        )


def to_candles(klines: list[list]) -> list[Candle]:
    return [Candle.from_binance(r) for r in klines]
