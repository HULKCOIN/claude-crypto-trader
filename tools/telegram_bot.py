#!/usr/bin/env python3
"""Telegram command bot: control the trading system from your phone.

Commands (type in chat with your bot):
  /start       — register & show help
  /help        — list commands
  /scan        — run scanner now, reply with top 5 setups
  /signals     — last 20 signals from the queue
  /positions   — currently open paper/testnet positions
  /sentiment   — fear & greed + funding
  /open SYM    — open a long on SYM at market (uses budget + risk)
  /close SYM   — close an open position on SYM
  /flat        — close ALL open positions
  /pause       — pause auto-trader
  /resume      — resume auto-trader
  /status      — mode (spot/futures, paper/testnet/live), equity, open PnL

Security:
  Only chat IDs listed in TELEGRAM_CHAT_ID env var (comma-separated for
  multi-user) may issue commands. Anyone else gets ignored silently.

Runs as a long-polling daemon:
  python3 tools/telegram_bot.py
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import BRIDGE_DIR, iso_now, load_config  # noqa: E402


REPO_ROOT = Path(__file__).resolve().parent.parent
PAUSE_FLAG = BRIDGE_DIR / "auto_trader.paused"


def _tg(method: str, **params) -> dict:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN missing")
    url = f"https://api.telegram.org/bot{token}/{method}"
    data = urlencode(params).encode()
    with urlopen(Request(url, data=data), timeout=30) as r:
        return json.loads(r.read().decode())


def send(chat_id: int | str, text: str, parse_mode: str = "Markdown") -> None:
    try:
        _tg("sendMessage", chat_id=chat_id, text=text[:4000], parse_mode=parse_mode)
    except Exception as e:
        print(f"[tg] send failed: {e}", file=sys.stderr)


def allowed_chat_ids() -> set[str]:
    """Owner-only: NUR die Chat-IDs aus TELEGRAM_OWNER_CHAT_ID (bevorzugt)
    oder TELEGRAM_CHAT_ID als Fallback dürfen Commands auslösen.

    WICHTIG: TELEGRAM_NEWS_CHAT_ID (die öffentliche News-Gruppe) steht hier
    bewusst NICHT drin. Niemand außer dem Owner darf den Trading-Bot steuern.
    """
    raw = os.environ.get("TELEGRAM_OWNER_CHAT_ID") or os.environ.get("TELEGRAM_CHAT_ID", "")
    ids = {c.strip() for c in raw.split(",") if c.strip()}
    # Explizit die News-Gruppe sperren, falls sie versehentlich in TELEGRAM_CHAT_ID landet
    news_chat = (os.environ.get("TELEGRAM_NEWS_CHAT_ID") or "").strip()
    if news_chat and news_chat in ids:
        ids.discard(news_chat)
    return ids


# --- command handlers ---

def cmd_help() -> str:
    return (
        "*Crypto Signals Bot*\n\n"
        "/scan — scan market now\n"
        "/signals — last 20 signals\n"
        "/positions — open positions\n"
        "/sentiment — fear & greed + funding\n"
        "/open SYM — open long at market\n"
        "/close SYM — close a position\n"
        "/flat — close ALL positions\n"
        "/pause /resume — auto-trader on/off\n"
        "/status — mode & equity"
    )


def _py(script: str, *args: str) -> str:
    """Run a tool script, return its stdout."""
    try:
        proc = subprocess.run(
            [sys.executable, str(REPO_ROOT / "tools" / script), *args],
            capture_output=True, text=True, timeout=120, cwd=str(REPO_ROOT),
        )
        return proc.stdout or proc.stderr
    except Exception as e:
        return f"error: {e}"


def cmd_scan() -> str:
    out = _py("scanner.py", "--top-n", "20")
    try:
        hits = json.loads(out.strip().split("\n")[0]) if out.strip() else []
    except Exception:
        hits = []
    if not hits:
        return "No setups right now."
    lines = ["*Top setups:*"]
    for h in hits[:5]:
        lines.append(f"• `{h.get('symbol')}` {h.get('setup')} @ {h.get('price')} "
                     f"(Q={h.get('quality')}, volZ={h.get('volume_zscore', '-')})")
    return "\n".join(lines)


def cmd_signals() -> str:
    q = BRIDGE_DIR / "signal_queue.jsonl"
    if not q.exists():
        return "signal queue empty"
    tail = q.read_text().splitlines()[-20:]
    lines = ["*Last signals:*"]
    for l in tail:
        try:
            s = json.loads(l)
            lines.append(f"• `{s.get('source')}` {s.get('symbol', '')} "
                         f"{s.get('setup') or s.get('verdict') or s.get('signal') or ''}")
        except Exception:
            continue
    return "\n".join(lines[:20])


def cmd_positions() -> str:
    cfg = load_config()
    mode = cfg["broker"]["mode"]
    if mode == "paper":
        fills = BRIDGE_DIR / "paper_fills.jsonl"
        if not fills.exists():
            return "no paper positions"
        rows = [json.loads(l) for l in fills.read_text().splitlines()[-10:]]
        return "*Paper fills (last 10):*\n" + "\n".join(
            f"• `{r['symbol']}` {r['side']} {r['qty']} @ {r['entry']}" for r in rows
        )
    # testnet / live
    try:
        from tools.broker import _ccxt_exchange
        ex = _ccxt_exchange(cfg)
        if cfg["broker"].get("market") == "futures":
            positions = ex.fetch_positions()
            open_pos = [p for p in positions if float(p.get("contracts", 0)) != 0]
            if not open_pos:
                return "no open positions"
            return "*Open positions:*\n" + "\n".join(
                f"• `{p['symbol']}` {p['side']} {p['contracts']} pnl={p.get('unrealizedPnl')}" for p in open_pos
            )
        else:
            bal = ex.fetch_balance()
            holdings = {k: v for k, v in bal.get("total", {}).items() if v and v > 0 and k != "USDT"}
            if not holdings:
                return "no spot holdings besides USDT"
            return "*Spot holdings:*\n" + "\n".join(f"• `{k}`: {v}" for k, v in holdings.items())
    except Exception as e:
        return f"error: {e}"


def cmd_sentiment() -> str:
    out = _py("sentiment.py")
    try:
        d = json.loads(out)
        return (f"*Market sentiment:*\nF&G: {d.get('fear_greed_index')} ({d.get('fear_greed_label')})\n"
                f"Funding 8h: {d.get('funding_rate_8h', '-')}\nL/S: {d.get('long_short_ratio', '-')}\n"
                f"Verdict: *{d.get('verdict')}*")
    except Exception:
        return out[:500] or "no data"


def cmd_open(symbol: str) -> str:
    if not symbol:
        return "usage: /open BTCUSDT"
    # Full pipeline: scanner → exit → timemachine → risk → budget → broker create+approve+place
    symbol = symbol.upper()
    try:
        from tools._common import binance_klines
        klines = binance_klines(symbol, interval="1m", limit=1)
        price = float(klines[-1][4])
    except Exception as e:
        return f"could not fetch {symbol}: {e}"
    exit_out = _py("exit.py", "--symbol", symbol, "--entry", str(price))
    try:
        exit_data = json.loads(exit_out)
        plan_path = exit_data.get("plan_path") or f"bridge/plans/{symbol}_latest.json"
    except Exception:
        return f"exit failed: {exit_out[:400]}"

    plan_abs = str(REPO_ROOT / plan_path) if not plan_path.startswith("/") else plan_path
    risk_out = _py("risk.py", "--plan", plan_abs)
    budget_out = _py("budget.py", "--plan", plan_abs)
    size_path = BRIDGE_DIR / f"size_{symbol}.json"
    size_path.write_text(budget_out)

    _py("broker.py", "create", "--plan", plan_abs, "--size", str(size_path))
    _py("broker.py", "approve", "--index", "1", "--reply", "yes")
    place_out = _py("broker.py", "place", "--index", "1")
    return f"*/open {symbol}* done\n```{place_out[:800]}```"


def cmd_close(symbol: str) -> str:
    cfg = load_config()
    if cfg["broker"]["mode"] == "paper":
        return f"paper mode — mark {symbol} closed in bridge/paper_fills.jsonl manually"
    try:
        from tools.broker import _ccxt_exchange
        ex = _ccxt_exchange(cfg)
        sym = symbol.upper()
        if "/" not in sym:
            sym = sym[:-4] + "/" + sym[-4:]
        if cfg["broker"].get("market") == "futures":
            positions = ex.fetch_positions([sym])
            for p in positions:
                qty = abs(float(p.get("contracts", 0)))
                if qty == 0:
                    continue
                side = "sell" if p.get("side") == "long" else "buy"
                order = ex.create_market_order(sym, side, qty, params={"reduceOnly": True})
                return f"closed {sym}: {json.dumps(order, default=str)[:400]}"
            return f"no open position on {sym}"
        else:
            bal = ex.fetch_balance()
            base = sym.split("/")[0]
            qty = bal.get("total", {}).get(base, 0)
            if qty <= 0:
                return f"no {base} to sell"
            order = ex.create_market_order(sym, "sell", qty)
            return f"sold {qty} {base}: {json.dumps(order, default=str)[:400]}"
    except Exception as e:
        return f"error: {e}"


def cmd_flat() -> str:
    cfg = load_config()
    if cfg["broker"]["mode"] == "paper":
        return "paper mode — nothing to flatten"
    try:
        from tools.broker import _ccxt_exchange
        ex = _ccxt_exchange(cfg)
        if cfg["broker"].get("market") == "futures":
            positions = ex.fetch_positions()
            closed = []
            for p in positions:
                qty = abs(float(p.get("contracts", 0)))
                if qty == 0:
                    continue
                side = "sell" if p.get("side") == "long" else "buy"
                ex.create_market_order(p["symbol"], side, qty, params={"reduceOnly": True})
                closed.append(p["symbol"])
            return f"flattened: {', '.join(closed) or 'nothing open'}"
        return "spot flat: liquidate individual holdings via /close"
    except Exception as e:
        return f"error: {e}"


def cmd_pause() -> str:
    BRIDGE_DIR.mkdir(parents=True, exist_ok=True)
    PAUSE_FLAG.write_text(iso_now())
    return "⏸ auto-trader paused"


def cmd_resume() -> str:
    if PAUSE_FLAG.exists():
        PAUSE_FLAG.unlink()
    return "▶️ auto-trader resumed"


def cmd_status() -> str:
    cfg = load_config()
    b = cfg["broker"]
    paused = "yes" if PAUSE_FLAG.exists() else "no"
    return (f"*Status*\nexchange: `{b['exchange']}` market: `{b.get('market','spot')}` mode: `{b['mode']}`\n"
            f"paused: {paused}\nrisk/trade: {cfg['budget']['risk_per_trade_pct']}% of ${cfg['budget']['account_equity_usd']}")


COMMANDS = {
    "/start": lambda _args: cmd_help(),
    "/help": lambda _args: cmd_help(),
    "/scan": lambda _args: cmd_scan(),
    "/signals": lambda _args: cmd_signals(),
    "/positions": lambda _args: cmd_positions(),
    "/sentiment": lambda _args: cmd_sentiment(),
    "/open": lambda args: cmd_open(args[0] if args else ""),
    "/close": lambda args: cmd_close(args[0] if args else ""),
    "/flat": lambda _args: cmd_flat(),
    "/pause": lambda _args: cmd_pause(),
    "/resume": lambda _args: cmd_resume(),
    "/status": lambda _args: cmd_status(),
}


def handle(update: dict) -> None:
    msg = update.get("message") or update.get("edited_message") or {}
    chat_id = str((msg.get("chat") or {}).get("id", ""))
    text = (msg.get("text") or "").strip()
    if not chat_id or not text.startswith("/"):
        return
    allowed = allowed_chat_ids()
    # Fail-closed: wenn keine Allowlist konfiguriert ist, NIEMAND darf steuern
    if not allowed:
        print(f"[tg] REFUSED (no allowlist): {chat_id}", file=sys.stderr)
        return
    if chat_id not in allowed:
        # Zusätzlich blockieren falls Message aus News-Gruppe kommt
        print(f"[tg] REFUSED unauthorized chat {chat_id}", file=sys.stderr)
        return
    parts = text.split()
    cmd = parts[0].split("@")[0].lower()  # strip @botname if group chat
    args = parts[1:]
    handler = COMMANDS.get(cmd)
    if not handler:
        send(chat_id, f"unknown command. try /help")
        return
    try:
        reply = handler(args)
    except Exception as e:
        reply = f"error: {e}"
    send(chat_id, reply)


def main() -> int:
    if not os.environ.get("TELEGRAM_BOT_TOKEN"):
        print("TELEGRAM_BOT_TOKEN missing", file=sys.stderr)
        return 2
    offset = 0
    print("[tg] long-polling started", file=sys.stderr)
    while True:
        try:
            resp = _tg("getUpdates", offset=offset, timeout=25)
            for u in resp.get("result", []):
                offset = u["update_id"] + 1
                handle(u)
        except KeyboardInterrupt:
            return 0
        except Exception as e:
            print(f"[tg] poll error: {e}", file=sys.stderr)
            time.sleep(5)


if __name__ == "__main__":
    sys.exit(main())
