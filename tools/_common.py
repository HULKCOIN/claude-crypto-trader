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


# --- Market data via Coinbase Exchange (US-based, always reachable) ---
# Binance.com AND Bybit both return 403 from GitHub Actions US runners.
# Coinbase is a US exchange so its public API has no geo-restrictions.
# Interval: Coinbase uses seconds (900 = 15min, 3600 = 1h, 86400 = 1d).
# Pair format: BTC-USD (dash, USD quote). We normalize to BTCUSDT shape
# for compatibility with the rest of the scanner.

COINBASE_BASE = "https://api.exchange.coinbase.com"
_BINANCE_HOSTS = ["https://api-gcp.binance.com", "https://api.binance.com"]

# Top liquid Coinbase USD pairs — hardcoded to avoid 150+ stats calls per scan.
# Keep BTC/ETH/majors + enough altcoins for setup variety.
COINBASE_TOP_USD = [
    "BTC-USD", "ETH-USD", "SOL-USD", "XRP-USD", "DOGE-USD",
    "ADA-USD", "AVAX-USD", "LINK-USD", "DOT-USD", "LTC-USD",
    "SHIB-USD", "ATOM-USD", "UNI-USD", "BCH-USD", "NEAR-USD",
    "APT-USD", "ARB-USD", "OP-USD", "FIL-USD", "INJ-USD",
    "SUI-USD", "SEI-USD", "TIA-USD", "FET-USD", "RENDER-USD",
    "HBAR-USD", "VET-USD", "AAVE-USD", "MKR-USD", "ICP-USD",
]

_COINBASE_GRAN = {"1m": 60, "5m": 300, "15m": 900, "1h": 3600, "6h": 21600, "1d": 86400}


def _to_binance_sym(coinbase_id: str) -> str:
    """BTC-USD -> BTCUSDT (so downstream scanner labels look consistent)."""
    return coinbase_id.replace("-USD", "USDT").replace("-", "")


def _to_coinbase_product(binance_sym: str) -> str:
    """BTCUSDT -> BTC-USD. Falls back to as-is if already dash-format."""
    if "-" in binance_sym:
        return binance_sym
    for suffix in ("USDT", "USDC", "USD"):
        if binance_sym.endswith(suffix):
            base = binance_sym[: -len(suffix)]
            return f"{base}-USD"
    return binance_sym


def _coinbase_candles(product_id: str, interval: str, limit: int) -> list[list]:
    gran = _COINBASE_GRAN.get(interval, 900)
    rows = http_get(f"{COINBASE_BASE}/products/{product_id}/candles", {"granularity": gran})
    # Coinbase returns newest-first: [time_seconds, low, high, open, close, volume]
    out = []
    for r in list(reversed(rows))[-limit:]:
        time_s, low, high, open_, close, volume = r
        out.append([
            int(time_s) * 1000,          # open_time ms
            float(open_), float(high), float(low), float(close), float(volume),
            int(time_s) * 1000 + gran * 1000,  # close_time ms
            0, 0, 0, 0, 0,                 # padding to match Binance 12-column layout
        ])
    return out


def binance_klines(symbol: str, interval: str = "15m", limit: int = 100) -> list[list]:
    """Market klines; routes to Coinbase (US-based, reliable from GitHub Actions)."""
    import sys as _sys
    product = _to_coinbase_product(symbol)
    try:
        return _coinbase_candles(product, interval, limit)
    except Exception as e:
        print(f"[market] coinbase kline for {product} failed: {e}; trying Binance mirrors", file=_sys.stderr)
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
    """Top pairs by 24h USD volume via Coinbase stats on a curated universe.

    We query the stats endpoint for each of ~30 known-liquid pairs (one HTTP
    request each — Coinbase's public rate limit is 10/s). This takes ~3 s but
    avoids maintaining a dynamic pair list.
    """
    import sys as _sys
    rows: list[tuple[str, float]] = []
    for product in COINBASE_TOP_USD[:top_n * 2]:  # fetch 2× top_n candidates
        try:
            stats = http_get(f"{COINBASE_BASE}/products/{product}/stats")
            last = float(stats.get("last", 0) or 0)
            vol_base = float(stats.get("volume", 0) or 0)
            vol_usd = last * vol_base
            if vol_usd >= min_vol_usd:
                rows.append((_to_binance_sym(product), vol_usd))
        except Exception as e:
            print(f"[market] coinbase stats {product}: {e}", file=_sys.stderr)
            continue
    rows.sort(key=lambda x: x[1], reverse=True)
    print(f"[market] coinbase: {len(rows)} pairs above ${min_vol_usd:,.0f} volume", file=_sys.stderr)
    return [s for s, _ in rows[:top_n]]


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
