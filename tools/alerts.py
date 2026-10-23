#!/usr/bin/env python3
"""crypto-alerts: price/level alerts via Telegram, Discord or stdout."""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from urllib.request import Request, urlopen

from _common import BRIDGE_DIR, binance_klines, load_config


ALERTS_FILE = BRIDGE_DIR / "alerts.json"


def load_alerts() -> list[dict]:
    if ALERTS_FILE.exists():
        return json.loads(ALERTS_FILE.read_text())
    return []


def save_alerts(alerts: list[dict]) -> None:
    BRIDGE_DIR.mkdir(parents=True, exist_ok=True)
    ALERTS_FILE.write_text(json.dumps(alerts, indent=2))


def push(message: str, cfg: dict) -> None:
    sent = False
    tg_token = os.environ.get(cfg["alerts"]["telegram_bot_token_env"])
    tg_chat = os.environ.get(cfg["alerts"]["telegram_chat_id_env"])
    if tg_token and tg_chat:
        try:
            url = f"https://api.telegram.org/bot{tg_token}/sendMessage"
            data = json.dumps({"chat_id": tg_chat, "text": message}).encode()
            req = Request(url, data=data, headers={"Content-Type": "application/json"})
            urlopen(req, timeout=10).read()
            sent = True
        except Exception as e:
            print(f"[alerts] telegram failed: {e}", file=sys.stderr)
    discord = os.environ.get(cfg["alerts"]["discord_webhook_env"])
    if discord:
        try:
            data = json.dumps({"content": message}).encode()
            req = Request(discord, data=data, headers={"Content-Type": "application/json"})
            urlopen(req, timeout=10).read()
            sent = True
        except Exception as e:
            print(f"[alerts] discord failed: {e}", file=sys.stderr)
    if not sent:
        print(f"[alert] {message}")


def cmd_add(args) -> int:
    alerts = load_alerts()
    alerts.append({
        "id": int(time.time() * 1000),
        "symbol": args.symbol.upper(),
        "above": args.above,
        "below": args.below,
        "created_ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "triggered": False,
    })
    save_alerts(alerts)
    print(json.dumps(alerts[-1], indent=2))
    return 0


def cmd_list(_args) -> int:
    print(json.dumps(load_alerts(), indent=2))
    return 0


def cmd_watch(_args) -> int:
    cfg = load_config()
    alerts = load_alerts()
    changed = False
    for a in alerts:
        if a["triggered"]:
            continue
        klines = binance_klines(a["symbol"], interval="1m", limit=1)
        price = float(klines[-1][4])
        if a.get("above") and price >= a["above"]:
            push(f"🚨 {a['symbol']} above {a['above']} (now {price})", cfg)
            a["triggered"] = True
            changed = True
        elif a.get("below") and price <= a["below"]:
            push(f"🔻 {a['symbol']} below {a['below']} (now {price})", cfg)
            a["triggered"] = True
            changed = True
    if changed:
        save_alerts(alerts)
    return 0


def main() -> int:
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)
    pa = sub.add_parser("add")
    pa.add_argument("--symbol", required=True)
    pa.add_argument("--above", type=float)
    pa.add_argument("--below", type=float)
    pa.set_defaults(fn=cmd_add)
    pl = sub.add_parser("list"); pl.set_defaults(fn=cmd_list)
    pw = sub.add_parser("watch"); pw.set_defaults(fn=cmd_watch)
    args = p.parse_args()
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
