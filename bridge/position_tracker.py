#!/usr/bin/env python3
"""Position Tracker — schließt Trades, pusht Close-Notifications, kumuliert PnL.

Läuft nach jedem Pipeline-Durchlauf. Für jede offene Position:
  1. Holt Candles seit Entry (Yahoo via binance_klines)
  2. Walk bars chronologisch — was wurde zuerst getroffen: Stop oder Target?
  3. Falls getroffen: markiert als CLOSED, berechnet PnL, pusht Telegram
  4. Aktualisiert data/snapshots/pnl_stats.json (Gesamt-PnL, Win-Rate, etc.)

Datenformat auto_trades.jsonl (eine JSON-Zeile pro Trade):
  { "ts": "...", "symbol": "...", "entry": X, "stop": Y, "target": Z, ... }

Nach Close zusätzlich:
  { ..., "closed": true, "close_ts": "...", "close_price": X,
    "pnl_usd": X, "pnl_pct": X, "duration_min": X, "exit_reason": "stop|target|open" }
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
from tools._common import BRIDGE_DIR, binance_klines, iso_now  # noqa: E402


AUTO_LOG = BRIDGE_DIR / "auto_trades.jsonl"
AUTO_LOG_SNAPSHOT = REPO / "data" / "snapshots" / "auto_trades.jsonl"
PNL_STATS = REPO / "data" / "snapshots" / "pnl_stats.json"


def _restore_auto_log() -> None:
    """CI runners start fresh — restore the auto-trade log from the committed snapshot."""
    if not AUTO_LOG.exists() and AUTO_LOG_SNAPSHOT.exists():
        AUTO_LOG.parent.mkdir(parents=True, exist_ok=True)
        AUTO_LOG.write_text(AUTO_LOG_SNAPSHOT.read_text())


def tg_notify(text: str) -> None:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat = os.environ.get("TELEGRAM_CHAT_ID")
    if not (token and chat):
        return
    try:
        urlopen(Request(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data=urlencode({"chat_id": chat, "text": text[:4000],
                            "parse_mode": "Markdown"}).encode(),
        ), timeout=15).read()
    except Exception as e:
        print(f"[tracker] tg notify failed: {e}", file=sys.stderr)


def load_trades() -> list[dict]:
    if not AUTO_LOG.exists():
        return []
    out = []
    for line in AUTO_LOG.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def save_trades(trades: list[dict]) -> None:
    AUTO_LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(AUTO_LOG, "w") as f:
        for t in trades:
            f.write(json.dumps(t) + "\n")


def load_stats() -> dict:
    if PNL_STATS.exists():
        try:
            return json.loads(PNL_STATS.read_text())
        except Exception:
            pass
    return {
        "total_pnl_usd": 0.0,
        "total_pnl_pct_avg": 0.0,
        "trades_total": 0,
        "trades_wins": 0,
        "trades_losses": 0,
        "trades_timeout": 0,
        "biggest_win_usd": 0.0,
        "biggest_loss_usd": 0.0,
        "last_updated": iso_now(),
    }


def save_stats(stats: dict) -> None:
    PNL_STATS.parent.mkdir(parents=True, exist_ok=True)
    PNL_STATS.write_text(json.dumps(stats, indent=2))


def check_position_closed(trade: dict) -> dict | None:
    """Fetcht Candles seit Entry und checkt bar-by-bar ob Stop oder Target zuerst hit.
    Returns Close-Infos oder None (noch offen).
    """
    symbol = trade["symbol"]
    entry = float(trade["entry"])
    stop = float(trade["stop"])
    target = float(trade["target"])
    qty = float(trade.get("qty", 0) or 0)
    side = trade.get("side", "long")
    open_ts = trade["ts"]

    try:
        # Hole genügend bars seit Open (worst case: alte Trades brauchen viele)
        klines = binance_klines(symbol, interval="15m", limit=200)
    except Exception as e:
        print(f"[tracker] kline fetch failed for {symbol}: {e}", file=sys.stderr)
        return None

    # Filter zu Candles NACH dem Open
    import time
    try:
        open_epoch_ms = int(time.mktime(time.strptime(open_ts, "%Y-%m-%dT%H:%M:%SZ"))) * 1000
    except Exception:
        return None

    relevant = [k for k in klines if int(k[0]) >= open_epoch_ms]
    if not relevant:
        return None

    for bar in relevant:
        bar_time = int(bar[0])
        high = float(bar[2])
        low = float(bar[3])

        if side == "long":
            # Target zuerst wenn high >= target BEVOR low <= stop
            hit_target = high >= target
            hit_stop = low <= stop
            if hit_target and hit_stop:
                # Ambiguous — konservativ: Stop zuerst (Worst-Case)
                exit_reason = "stop"
                close_price = stop
            elif hit_target:
                exit_reason = "target"
                close_price = target
            elif hit_stop:
                exit_reason = "stop"
                close_price = stop
            else:
                continue
        else:  # short
            hit_target = low <= target
            hit_stop = high >= stop
            if hit_target and hit_stop:
                exit_reason = "stop"
                close_price = stop
            elif hit_target:
                exit_reason = "target"
                close_price = target
            elif hit_stop:
                exit_reason = "stop"
                close_price = stop
            else:
                continue

        # Position wurde geschlossen in diesem bar
        if side == "long":
            pnl_pct = (close_price / entry - 1) * 100
            pnl_usd = qty * (close_price - entry)
        else:
            pnl_pct = (entry / close_price - 1) * 100
            pnl_usd = qty * (entry - close_price)

        close_time_s = bar_time // 1000
        close_ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(close_time_s))
        duration_sec = close_time_s - (open_epoch_ms // 1000)
        duration_min = duration_sec // 60

        return {
            "exit_reason": exit_reason,
            "close_price": close_price,
            "close_ts": close_ts,
            "pnl_usd": round(pnl_usd, 2),
            "pnl_pct": round(pnl_pct, 3),
            "duration_min": duration_min,
        }

    return None  # noch offen


def format_close_message(trade: dict, close: dict, stats_before: dict,
                         stats_after: dict) -> str:
    icon = "🎯" if close["exit_reason"] == "target" else "🛑"
    pnl_sign = "+" if close["pnl_usd"] >= 0 else ""
    duration_h = close["duration_min"] / 60
    duration_str = f"{int(duration_h)}h {close['duration_min'] % 60}min" if duration_h >= 1 else f"{close['duration_min']}min"

    verdict = "GEWINN ✅" if close["pnl_usd"] >= 0 else "VERLUST ❌"
    total_change = stats_after["total_pnl_usd"] - stats_before["total_pnl_usd"]
    total_sign = "+" if stats_after["total_pnl_usd"] >= 0 else ""
    wr = (stats_after["trades_wins"] / stats_after["trades_total"] * 100) if stats_after["trades_total"] else 0

    return (
        f"{icon} *POSITION GESCHLOSSEN — {verdict}*\n"
        f"`{trade['symbol']}` ({trade.get('setup', '?')})\n\n"
        f"*📊 Trade-Abschluss:*\n"
        f"• Einstieg: ${trade['entry']:.6f}\n"
        f"• Ausstieg: ${close['close_price']:.6f} ({close['exit_reason'].upper()})\n"
        f"• Dauer: {duration_str}\n"
        f"• Menge: {trade.get('qty', 0):.6f}\n\n"
        f"*💰 PnL dieser Trade:*\n"
        f"• USD: *{pnl_sign}${close['pnl_usd']:,.2f}*\n"
        f"• Prozent: *{pnl_sign}{close['pnl_pct']:+.2f}%*\n\n"
        f"*📈 Gesamt-PnL seit Start:*\n"
        f"• Gesamt: *{total_sign}${stats_after['total_pnl_usd']:,.2f}*\n"
        f"• Trades: {stats_after['trades_total']} ({stats_after['trades_wins']}W / {stats_after['trades_losses']}L)\n"
        f"• Win-Rate: *{wr:.1f}%*\n"
        f"• Größter Win: ${stats_after['biggest_win_usd']:,.2f}\n"
        f"• Größter Loss: ${stats_after['biggest_loss_usd']:,.2f}"
    )


def main() -> int:
    _restore_auto_log()
    trades = load_trades()
    if not trades:
        print("[tracker] no trades yet")
        return 0

    stats = load_stats()
    stats_before = dict(stats)
    changed = False

    for i, trade in enumerate(trades):
        if trade.get("closed"):
            continue
        print(f"[tracker] checking {trade.get('symbol')} opened at {trade.get('ts')}")

        close = check_position_closed(trade)
        if not close:
            print(f"[tracker]   still open")
            continue

        print(f"[tracker]   CLOSED: {close['exit_reason']} @ {close['close_price']} "
              f"PnL ${close['pnl_usd']:+.2f} ({close['pnl_pct']:+.2f}%)")

        # Update trade in-place
        trades[i] = {**trade, "closed": True, **close}

        # Update stats
        stats["trades_total"] += 1
        if close["pnl_usd"] > 0:
            stats["trades_wins"] += 1
        elif close["pnl_usd"] < 0:
            stats["trades_losses"] += 1
        stats["total_pnl_usd"] = round(stats["total_pnl_usd"] + close["pnl_usd"], 2)
        if close["pnl_usd"] > stats["biggest_win_usd"]:
            stats["biggest_win_usd"] = round(close["pnl_usd"], 2)
        if close["pnl_usd"] < stats["biggest_loss_usd"]:
            stats["biggest_loss_usd"] = round(close["pnl_usd"], 2)
        stats["last_updated"] = iso_now()

        # Push notification
        tg_notify(format_close_message(trade, close, stats_before, stats))
        changed = True

    if changed:
        save_trades(trades)
        save_stats(stats)
        print(f"[tracker] updated {sum(1 for t in trades if t.get('closed'))} total closed trades")
    else:
        print("[tracker] nothing closed this round")

    return 0


if __name__ == "__main__":
    sys.exit(main())
