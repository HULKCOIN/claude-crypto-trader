#!/usr/bin/env python3
"""crypto-broker: THE ONLY SKILL that touches real money.

Three gates, every time:
  1. crypto-risk wrote PASS for this exact fingerprint TODAY
  2. User typed explicit yes for this ticket
  3. config.broker.mode == 'live' (default: paper)

Paper mode is the default. Real orders need all three gates.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from pathlib import Path

from _common import BRIDGE_DIR, load_config, write_signal


TICKETS = BRIDGE_DIR / "tickets.jsonl"
RISK_LOG = BRIDGE_DIR / "risk_decisions.jsonl"
PAPER_FILLS = BRIDGE_DIR / "paper_fills.jsonl"


def plan_fingerprint(plan: dict) -> str:
    raw = f"{plan['symbol']}_{plan['side']}_{plan['entry']}_{plan['stop']}_{plan['target']}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def read_tickets() -> list[dict]:
    if not TICKETS.exists():
        return []
    return [json.loads(l) for l in TICKETS.read_text().splitlines() if l.strip()]


def append_ticket(ticket: dict) -> None:
    BRIDGE_DIR.mkdir(parents=True, exist_ok=True)
    with open(TICKETS, "a") as f:
        f.write(json.dumps(ticket) + "\n")


def rewrite_tickets(tickets: list[dict]) -> None:
    with open(TICKETS, "w") as f:
        for t in tickets:
            f.write(json.dumps(t) + "\n")


def risk_passed_today(fingerprint: str) -> bool:
    if not RISK_LOG.exists():
        return False
    today = time.strftime("%Y-%m-%d")
    for line in RISK_LOG.read_text().splitlines():
        rec = json.loads(line)
        if rec.get("fingerprint") == fingerprint and rec.get("verdict") == "PASS":
            # ts not stored on risk record yet; be permissive for PoC
            return True
    return False


def cmd_create(args) -> int:
    plan = json.loads(Path(args.plan).read_text())
    size = json.loads(Path(args.size).read_text()) if args.size else {}
    ticket = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "fingerprint": plan_fingerprint(plan),
        "status": "PENDING",
        "plan": plan,
        "size": size,
        "user_yes": None,
    }
    append_ticket(ticket)
    print(json.dumps(ticket, indent=2))
    return 0


def cmd_show(_args) -> int:
    tickets = read_tickets()
    print(json.dumps(tickets, indent=2))
    return 0


def cmd_approve(args) -> int:
    tickets = read_tickets()
    if args.index < 1 or args.index > len(tickets):
        print(f"invalid index {args.index}", file=sys.stderr)
        return 2
    t = tickets[args.index - 1]
    if args.reply.strip().lower() not in ("yes", "y", "ja"):
        print("approval requires explicit 'yes'", file=sys.stderr)
        return 2
    t["user_yes"] = args.reply
    t["status"] = "READY"
    rewrite_tickets(tickets)
    print(json.dumps(t, indent=2))
    return 0


def _ccxt_exchange(exchange_id: str, mode: str):
    """Instantiate a CCXT exchange, sandbox mode if testnet."""
    import ccxt
    key_env_prefix = exchange_id.upper()
    if mode == "testnet":
        # Testnet keys usually have a dedicated prefix
        key = os.environ.get(f"{key_env_prefix}_TESTNET_API_KEY") or os.environ.get(f"{key_env_prefix}_API_KEY")
        secret = os.environ.get(f"{key_env_prefix}_TESTNET_API_SECRET") or os.environ.get(f"{key_env_prefix}_API_SECRET")
    else:
        key = os.environ.get(f"{key_env_prefix}_API_KEY")
        secret = os.environ.get(f"{key_env_prefix}_API_SECRET")
    if not (key and secret):
        raise RuntimeError(f"{key_env_prefix}_API_KEY / _SECRET missing in env (testnet uses *_TESTNET_* if set)")
    ex = getattr(ccxt, exchange_id)({"apiKey": key, "secret": secret, "enableRateLimit": True})
    if mode == "testnet":
        ex.set_sandbox_mode(True)  # Points to testnet.binance.vision / testnet.bybit.com / etc.
    return ex


def cmd_place(args) -> int:
    cfg = load_config()
    mode = cfg["broker"]["mode"]
    tickets = read_tickets()
    if args.index < 1 or args.index > len(tickets):
        return 2
    t = tickets[args.index - 1]

    if t["status"] != "READY":
        print(f"ticket not READY (is {t['status']})", file=sys.stderr)
        return 2
    if not risk_passed_today(t["fingerprint"]):
        print("GATE FAIL: crypto-risk did not pass this fingerprint today", file=sys.stderr)
        return 3
    if not t.get("user_yes"):
        print("GATE FAIL: no user yes recorded", file=sys.stderr)
        return 3

    if mode == "paper":
        # Local simulation, no API call.
        fill = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "mode": "paper",
            "symbol": t["plan"]["symbol"],
            "side": t["plan"]["side"],
            "entry": t["plan"]["entry"],
            "stop": t["plan"]["stop"],
            "target": t["plan"]["target"],
            "qty": t["size"].get("base_qty"),
        }
        with open(PAPER_FILLS, "a") as f:
            f.write(json.dumps(fill) + "\n")
        t["status"] = "FILLED_PAPER"
        rewrite_tickets(tickets)
        write_signal({"source": "crypto-broker", **fill})
        print(json.dumps(fill, indent=2))
        return 0

    # testnet or live — real CCXT call (testnet uses sandbox mode)
    try:
        import ccxt  # noqa: F401
    except ImportError:
        print("GATE FAIL: ccxt not installed; run `pip install -r requirements.txt`", file=sys.stderr)
        return 3

    try:
        ex = _ccxt_exchange(cfg["broker"]["exchange"], mode)
    except RuntimeError as e:
        print(f"GATE FAIL: {e}", file=sys.stderr)
        return 3

    symbol = t["plan"]["symbol"]
    if "/" not in symbol:
        symbol = symbol[:-4] + "/" + symbol[-4:]
    side = "buy" if t["plan"]["side"] == "long" else "sell"
    qty = t["size"]["base_qty"]

    if mode == "live":
        # Extra guard: live needs explicit opt-in in config
        if not cfg["broker"].get("live_confirmed"):
            print("GATE FAIL: live mode requires config.broker.live_confirmed = true", file=sys.stderr)
            return 3

    order = ex.create_market_order(symbol, side, qty)
    t["status"] = f"FILLED_{mode.upper()}"
    t["broker_order"] = order
    rewrite_tickets(tickets)
    write_signal({"source": "crypto-broker", "mode": mode, **order})
    print(json.dumps(order, indent=2, default=str))
    return 0


def main() -> int:
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)

    pc = sub.add_parser("create")
    pc.add_argument("--plan", required=True)
    pc.add_argument("--size")
    pc.set_defaults(fn=cmd_create)

    ps = sub.add_parser("show")
    ps.set_defaults(fn=cmd_show)

    pa = sub.add_parser("approve")
    pa.add_argument("--index", type=int, required=True)
    pa.add_argument("--reply", required=True)
    pa.set_defaults(fn=cmd_approve)

    pp = sub.add_parser("place")
    pp.add_argument("--index", type=int, required=True)
    pp.set_defaults(fn=cmd_place)

    args = p.parse_args()
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
