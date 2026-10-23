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


# --- Market data via Bybit (works from US / GitHub Actions) ---
# Binance.com geoblocks US IPs including GitHub Actions runners.
# Bybit's public v5 API has the same USDT pair universe and no geoblock.
# We keep the function names `binance_*` for compatibility but route to
# Bybit by default; actual Binance stays available as a fallback for
# self-hosted runners outside the US.

BYBIT_BASE = "https://api.bybit.com"
_BINANCE_HOSTS = ["https://api-gcp.binance.com", "https://api.binance.com"]
_PRIMARY_SOURCE_CACHE: dict[str, str] = {}


_BYBIT_INTERVAL = {"1m": "1", "3m": "3", "5m": "5", "15m": "15", "30m": "30",
                   "1h": "60", "2h": "120", "4h": "240", "6h": "360",
                   "12h": "720", "1d": "D", "1w": "W", "1M": "M"}


def _bybit_klines(symbol: str, interval: str, limit: int) -> list[list]:
    """Return Binance-shaped klines rows [open_time, o, h, l, c, v, ...] via Bybit v5."""
    bi = _BYBIT_INTERVAL.get(interval, "15")
    data = http_get(f"{BYBIT_BASE}/v5/market/kline",
                    {"category": "spot", "symbol": symbol, "interval": bi, "limit": limit})
    rows = data.get("result", {}).get("list", [])
    # Bybit returns newest first; invert and reshape to Binance layout
    out = []
    for r in reversed(rows):
        # Bybit: [startTime, open, high, low, close, volume, turnover]
        out.append([int(r[0]), r[1], r[2], r[3], r[4], r[5], int(r[0]) + 1, r[6], 0, 0, 0, 0])
    return out


def _bybit_top_pairs(quote: str, top_n: int, min_vol_usd: float) -> list[str]:
    data = http_get(f"{BYBIT_BASE}/v5/market/tickers", {"category": "spot"})
    rows = data.get("result", {}).get("list", [])
    picked = []
    for t in rows:
        sym = t.get("symbol", "")
        if not sym.endswith(quote):
            continue
        if any(x in sym for x in ("UP", "DOWN", "BULL", "BEAR", "3L", "3S", "5L", "5S")):
            continue
        vol = float(t.get("turnover24h", 0) or 0)  # already in USD-ish quote
        if vol < min_vol_usd:
            continue
        picked.append((sym, vol))
    picked.sort(key=lambda x: x[1], reverse=True)
    return [s for s, _ in picked[:top_n]]


def binance_klines(symbol: str, interval: str = "15m", limit: int = 100) -> list[list]:
    """Market klines; routes to Bybit by default (US-friendly)."""
    import sys as _sys
    try:
        return _bybit_klines(symbol, interval, limit)
    except Exception as e:
        print(f"[market] bybit kline for {symbol} failed: {e}; trying Binance mirrors", file=_sys.stderr)
    import urllib.error
    for host in _BINANCE_HOSTS:
        try:
            return http_get(f"{host}/api/v3/klines",
                            {"symbol": symbol, "interval": interval, "limit": limit})
        except urllib.error.HTTPError as e:
            if e.code in (451, 403, 418, 429):
                continue
            raise
        except Exception:
            continue
    raise RuntimeError(f"all market data sources failed for {symbol}")


def binance_top_pairs(quote: str = "USDT", top_n: int = 50, min_vol_usd: float = 5e7) -> list[str]:
    """Top pairs by 24h quote volume; routes to Bybit by default."""
    import sys as _sys
    try:
        pairs = _bybit_top_pairs(quote, top_n, min_vol_usd)
        if pairs:
            print(f"[market] bybit: {len(pairs)} {quote} pairs above ${min_vol_usd:,.0f} volume", file=_sys.stderr)
            return pairs
    except Exception as e:
        print(f"[market] bybit tickers failed: {e}", file=_sys.stderr)
    # Fallback to Binance mirrors (works on self-hosted non-US runners)
    import urllib.error
    for host in _BINANCE_HOSTS:
        try:
            tickers = http_get(f"{host}/api/v3/ticker/24hr")
            rows = [t for t in tickers
                    if t.get("symbol", "").endswith(quote)
                    and float(t.get("quoteVolume", 0) or 0) >= min_vol_usd
                    and not any(x in t.get("symbol", "") for x in ("UP", "DOWN", "BULL", "BEAR"))]
            rows.sort(key=lambda t: float(t.get("quoteVolume", 0) or 0), reverse=True)
            print(f"[market] binance mirror {host}: {len(rows)} pairs", file=_sys.stderr)
            return [t["symbol"] for t in rows[:top_n]]
        except urllib.error.HTTPError as e:
            if e.code in (451, 403, 418, 429):
                continue
            raise
        except Exception:
            continue
    return []


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
