"""Smoke tests that do NOT hit the network."""
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

from _common import atr, rsi, zscore, Candle  # noqa: E402
from scanner import detect_breakout, detect_flag, detect_oversold_bounce  # noqa: E402


def make_candles(prices, volumes=None):
    volumes = volumes or [1.0] * len(prices)
    return [
        Candle(open_time=i * 60_000, o=p, h=p * 1.001, l=p * 0.999, c=p, v=v)
        for i, (p, v) in enumerate(zip(prices, volumes))
    ]


def test_atr_sane():
    candles = make_candles([100, 101, 99, 102, 98, 103])
    a = atr([c.h for c in candles], [c.l for c in candles], [c.c for c in candles], 3)
    assert a > 0


def test_rsi_bounds():
    rising = make_candles([float(i) for i in range(1, 30)])
    r = rsi([c.c for c in rising])
    assert 70 <= r <= 100

    falling = make_candles([float(i) for i in range(30, 1, -1)])
    r = rsi([c.c for c in falling])
    assert 0 <= r <= 30


def test_zscore_zero_variance():
    assert zscore([1.0] * 30) == 0.0


def test_breakout_detects_volume_surge():
    # Prior volumes with real variance so z-score can clear 1.5
    import random
    rng = random.Random(42)
    prices = [100.0] * 30 + [105.0]
    vols = [rng.uniform(0.8, 1.2) for _ in range(30)] + [5.0]
    candles = make_candles(prices, vols)
    hit = detect_breakout(candles, lookback=20)
    assert hit is not None, "surge volume should trigger breakout"
    assert hit["setup"] == "breakout"
    assert hit["volume_zscore"] > 1.5


def test_breakout_rejects_no_volume():
    prices = [100.0] * 30 + [105.0]
    vols = [1.0] * 31
    candles = make_candles(prices, vols)
    assert detect_breakout(candles, lookback=20) is None


def test_oversold_bounce_requires_bullish_close():
    # Falling then one bar up: bullish close required
    prices = [float(x) for x in range(30, 1, -1)] + [5.0]
    candles = make_candles(prices)
    # Make the last candle bearish (close < open)
    last = candles[-1]
    bearish = Candle(open_time=last.open_time, o=last.o, h=last.h, l=last.l, c=last.o * 0.99, v=last.v)
    candles[-1] = bearish
    assert detect_oversold_bounce(candles) is None


if __name__ == "__main__":
    import traceback
    tests = [v for k, v in list(globals().items()) if k.startswith("test_")]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"PASS  {t.__name__}")
        except Exception:
            failed += 1
            print(f"FAIL  {t.__name__}")
            traceback.print_exc()
    sys.exit(1 if failed else 0)
