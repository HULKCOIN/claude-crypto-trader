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


# --- Market data: Yahoo Finance primary (Jake Schincariol pattern), Coinbase fallback ---
# Binance and Bybit return 403 from GitHub Actions US IPs. Coinbase works but
# has a limited USD-pair universe. Yahoo Finance:
#   - Not geoblocked (serves US + rest of world identically)
#   - Supports crypto via BTC-USD / ETH-USD / SOL-USD etc.
#   - Battle-tested fetch pattern from Jake's day-trader-skill (MIT)
#   - Spark endpoint lets us batch 20 symbols per request

YAHOO_HOSTS = ("https://query2.finance.yahoo.com", "https://query1.finance.yahoo.com")
YAHOO_UA = ("Mozilla/5.0 (compatible; claude-crypto-trader/0.1; "
            "+https://github.com/HULKCOIN/claude-crypto-trader)")
YAHOO_SPARK_MAX = 20
YAHOO_BACKOFF = 5.0

COINBASE_BASE = "https://api.exchange.coinbase.com"
_BINANCE_HOSTS = ["https://api-gcp.binance.com", "https://api.binance.com"]


class MarketError(Exception):
    pass


def _yahoo_get(path: str, timeout: int = 20) -> str:
    """Jake's yahoo_get: query2 first, query1 on 429/5xx, one retry after BACKOFF."""
    import sys as _sys
    import urllib.error
    last = "no answer"
    for attempt in (0, 1):
        saw_429 = False
        for host in YAHOO_HOSTS:
            try:
                req = Request(host + path, headers={"User-Agent": YAHOO_UA, "Accept": "*/*"})
                with urlopen(req, timeout=timeout) as r:
                    return r.read().decode("utf-8", "replace")
            except urllib.error.HTTPError as e:
                last = f"HTTP {e.code}"
                if e.code == 429:
                    saw_429 = True
                    continue
                if e.code >= 500:
                    continue
                try:
                    body = e.read().decode("utf-8", "replace")
                except Exception:
                    body = ""
                if body.strip().startswith("{"):
                    return body  # Yahoo's JSON error, let caller parse
                raise MarketError(f"Yahoo {last} for {path.split('?')[0]}")
            except Exception as e:
                last = str(e)
                continue
        if attempt == 0 and saw_429:
            import time as _t
            print(f"[yahoo] 429 — backing off {YAHOO_BACKOFF}s", file=_sys.stderr)
            _t.sleep(YAHOO_BACKOFF)
            continue
        break
    raise MarketError(f"Yahoo {last} for {path.split('?')[0]}")


_YAHOO_INTERVAL = {
    "1m": ("1m", "7d"), "2m": ("2m", "60d"), "5m": ("5m", "60d"),
    "15m": ("15m", "60d"), "30m": ("30m", "60d"), "1h": ("60m", "730d"),
    "1d": ("1d", "max"), "1wk": ("1wk", "max"),
}


def _to_yahoo_symbol(binance_sym: str) -> str:
    """BTCUSDT -> BTC-USD (Yahoo's crypto format)."""
    if "-" in binance_sym:
        return binance_sym.upper()
    for suffix in ("USDT", "USDC", "USD"):
        if binance_sym.endswith(suffix):
            return f"{binance_sym[:-len(suffix)].upper()}-USD"
    return binance_sym.upper()


def _yahoo_chart(symbol: str, interval: str, limit: int) -> list[list]:
    """Returns Binance-shaped kline rows via Yahoo's v8 chart endpoint."""
    y_int, y_range = _YAHOO_INTERVAL.get(interval, ("15m", "60d"))
    y_sym = _to_yahoo_symbol(symbol)
    raw = _yahoo_get(f"/v8/finance/chart/{y_sym}?range={y_range}&interval={y_int}")
    doc = json.loads(raw)
    chart = doc.get("chart") or {}
    res = chart.get("result") or []
    if not res:
        err = chart.get("error") or {}
        raise MarketError(f"Yahoo: no data for {y_sym} ({err.get('description', 'unknown')})")
    r0 = res[0]
    t = r0.get("timestamp") or []
    q = ((r0.get("indicators") or {}).get("quote") or [{}])[0] or {}
    opens, highs, lows, closes, vols = q.get("open") or [], q.get("high") or [], q.get("low") or [], q.get("close") or [], q.get("volume") or []
    out = []
    for i, ts in enumerate(t[-limit:] if len(t) > limit else t):
        idx = i if len(t) <= limit else i + (len(t) - limit)
        o, h, l, c, v = opens[idx], highs[idx], lows[idx], closes[idx], vols[idx]
        if c is None:
            continue
        out.append([
            int(ts) * 1000,
            float(o) if o is not None else float(c),
            float(h) if h is not None else float(c),
            float(l) if l is not None else float(c),
            float(c),
            float(v) if v is not None else 0.0,
            int(ts) * 1000 + 60_000,
            0, 0, 0, 0, 0,
        ])
    return out


# Top liquid Coinbase USD pairs — hardcoded to avoid 150+ stats calls per scan.
# Keep BTC/ETH/majors + enough altcoins for setup variety.
COINBASE_TOP_USD = [
    # Top-10 by Marktkap
    "BTC-USD", "ETH-USD", "SOL-USD", "XRP-USD", "DOGE-USD",
    "ADA-USD", "AVAX-USD", "LINK-USD", "DOT-USD", "LTC-USD",
    # Majors + Layer-1/2
    "SHIB-USD", "ATOM-USD", "UNI-USD", "BCH-USD", "NEAR-USD",
    "APT-USD", "ARB-USD", "OP-USD", "FIL-USD", "INJ-USD",
    "SUI-USD", "SEI-USD", "TIA-USD", "FET-USD", "RENDER-USD",
    "HBAR-USD", "VET-USD", "AAVE-USD", "ICP-USD", "ALGO-USD",
    # DeFi
    "GRT-USD", "CRV-USD", "COMP-USD", "SNX-USD", "YFI-USD",
    "LDO-USD", "MKR-USD", "SUSHI-USD", "1INCH-USD", "BAL-USD",
    # Gaming / Metaverse
    "SAND-USD", "MANA-USD", "APE-USD", "AXS-USD", "IMX-USD",
    "GALA-USD", "ENJ-USD", "CHZ-USD",
    # Memecoins (user interest)
    "PEPE-USD", "WIF-USD", "BONK-USD", "FLOKI-USD",
    # SOL-Ökosystem
    "JUP-USD", "PYTH-USD", "JTO-USD", "RAY-USD",
    # ETH-Ökosystem
    "ENS-USD", "LRC-USD",
    # Perp DEX / Derivatives
    "DYDX-USD", "GMX-USD",
    # AI / Big Data
    "JASMY-USD", "AKT-USD",
    # Payments / RWA
    "ONDO-USD", "QNT-USD",
    # Rohstoff-ähnliche
    "PAXG-USD",  # Pax Gold — on-chain Gold
]

# Yahoo-Tickers für Non-Crypto Marktdaten (Rohstoffe, Aktienindizes)
# Verwendbar manuell: binance_klines('GC=F', '1d') funktioniert via Yahoo-Primary
YAHOO_EXTRA_TICKERS = {
    # Rohstoffe (Futures)
    "GC=F": "Gold-Futures",
    "SI=F": "Silber-Futures",
    "CL=F": "WTI-Rohöl",
    "BZ=F": "Brent-Rohöl",
    "NG=F": "Erdgas",
    "HG=F": "Kupfer",
    # Stock-Indizes
    "^GSPC": "S&P 500",
    "^IXIC": "Nasdaq Composite",
    "^DJI": "Dow Jones",
    "^GDAXI": "DAX",
    "^FTSE": "FTSE 100",
    "^VIX": "Volatilitäts-Index",
    # FX / Alternative
    "DX=F": "US-Dollar-Index",
}

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
    """Market klines. Primary: Yahoo Finance (not geoblocked, works from US).
    Fallbacks: Coinbase, Binance mirrors."""
    import sys as _sys
    # 1) Yahoo (Jake's pattern, battle-tested, not geoblocked)
    try:
        return _yahoo_chart(symbol, interval, limit)
    except Exception as e:
        print(f"[market] yahoo kline for {symbol} failed: {e}; trying Coinbase", file=_sys.stderr)
    # 2) Coinbase (US-based)
    try:
        product = _to_coinbase_product(symbol)
        return _coinbase_candles(product, interval, limit)
    except Exception as e:
        print(f"[market] coinbase kline for {symbol} failed: {e}; trying Binance mirrors", file=_sys.stderr)
    # 3) Binance mirrors (works on self-hosted non-US runners)
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
    raise MarketError(f"all market data sources failed for {symbol}")


def binance_top_pairs(quote: str = "USDT", top_n: int = 50, min_vol_usd: float = 5e7) -> list[str]:
    """Top liquid crypto pairs. Uses Yahoo's spark endpoint for the regularMarketVolume
    across our curated universe; falls back to Coinbase stats if Yahoo rate-limits."""
    import sys as _sys
    # Universe for crypto: use Coinbase product list (same symbols work on Yahoo)
    universe_binance = [_to_binance_sym(p) for p in COINBASE_TOP_USD]
    universe_yahoo = [_to_yahoo_symbol(s) for s in universe_binance]

    # 1) Yahoo spark: batch volume + price lookup, 20 symbols at a time
    rows: list[tuple[str, float]] = []
    try:
        for batch_start in range(0, len(universe_yahoo), YAHOO_SPARK_MAX):
            batch = universe_yahoo[batch_start:batch_start + YAHOO_SPARK_MAX]
            path = f"/v7/finance/spark?symbols={','.join(batch)}&range=1d&interval=1h"
            raw = _yahoo_get(path)
            doc = json.loads(raw)
            spark = (doc or {}).get("spark") or {}
            for item in spark.get("result") or []:
                resp = item.get("response") or []
                if not resp:
                    continue
                meta = resp[0].get("meta") or {}
                price = meta.get("regularMarketPrice") or 0
                vol = meta.get("regularMarketVolume") or 0
                vol_usd = float(price) * float(vol)
                if vol_usd >= min_vol_usd:
                    y_sym = (item.get("symbol") or meta.get("symbol") or "").upper()
                    binance_sym = y_sym.replace("-USD", "USDT").replace("-", "")
                    rows.append((binance_sym, vol_usd))
        rows.sort(key=lambda x: x[1], reverse=True)
        if rows:
            print(f"[market] yahoo: {len(rows)} pairs above ${min_vol_usd:,.0f} volume", file=_sys.stderr)
            return [s for s, _ in rows[:top_n]]
    except Exception as e:
        print(f"[market] yahoo spark failed: {e}; falling back to Coinbase", file=_sys.stderr)

    # 2) Coinbase fallback
    for product in COINBASE_TOP_USD[:top_n * 2]:
        try:
            stats = http_get(f"{COINBASE_BASE}/products/{product}/stats")
            last = float(stats.get("last", 0) or 0)
            vol_base = float(stats.get("volume", 0) or 0)
            vol_usd = last * vol_base
            if vol_usd >= min_vol_usd:
                rows.append((_to_binance_sym(product), vol_usd))
        except Exception:
            continue
    rows.sort(key=lambda x: x[1], reverse=True)
    print(f"[market] coinbase fallback: {len(rows)} pairs", file=_sys.stderr)
    return [s for s, _ in rows[:top_n]]


# --- Jake's Wilder RSI (ported from day-trader-skill, MIT) ---

def wilder_rsi(closes: list[float], n: int = 14) -> float | None:
    """Wilder's RSI — the standard formulation. More accurate than SMA-based RSI."""
    if len(closes) < n + 1:
        return None
    gains = [max(0.0, closes[i] - closes[i - 1]) for i in range(1, len(closes))]
    losses = [max(0.0, closes[i - 1] - closes[i]) for i in range(1, len(closes))]
    ag, al = sum(gains[:n]) / n, sum(losses[:n]) / n
    for i in range(n, len(gains)):
        ag = (ag * (n - 1) + gains[i]) / n
        al = (al * (n - 1) + losses[i]) / n
    return 100 - 100 / (1 + ag / al) if al else 100.0


def find_setups_jake(closes: list[float]) -> tuple[list[str], dict]:
    """Jake's 5-setup detector, adapted for crypto (no session logic).
    closes: oldest first, >= 30 bars.
    Returns: (list of setup tags, dict of indicator numbers).
    """
    if len(closes) < 30:
        return [], {}
    last = closes[-1]
    hi20 = max(closes[-21:-1])
    ma20 = sum(closes[-20:]) / 20.0
    ma20_prev = sum(closes[-21:-1]) / 20.0
    run = closes[-6] / closes[-16] - 1  # 10-bar impulse ending 5 bars ago
    rng5 = (max(closes[-5:]) - min(closes[-5:])) / last
    r = wilder_rsi(closes)
    r_prev = wilder_rsi(closes[:-1])
    tags = []
    if last > hi20:
        tags.append("breakout_20d")
    if run > 0.10 and rng5 < 0.05:
        tags.append("bull_flag")
    if closes[-2] < ma20_prev and last > ma20:
        tags.append("reclaim_ma20")
    if r is not None and r < 30:
        tags.append("oversold")
    if r_prev is not None and r_prev < 30 and last > closes[-2]:
        tags.append("oversold_bounce")
    return tags, {
        "rsi": round(r, 1) if r is not None else None,
        "rsi_prev": round(r_prev, 1) if r_prev is not None else None,
        "high20": round(hi20, 8),
        "ma20": round(ma20, 8),
        "run_pct": round(run * 100, 2),
        "range5_pct": round(rng5 * 100, 2),
    }


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
