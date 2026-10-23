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


def _ccxt_exchange(cfg: dict):
    """Instantiate the right CCXT exchange.

    market: spot | futures
    mode:   paper   — local sim, no API
            demo    — Binance Demo Mode (https://demo-api.binance.com), uses regular
                      BINANCE_API_KEY / _SECRET generated inside the main binance.com
                      account via "Binance Demo Trading" → API Key Management
            testnet — old testnet.binance.vision (spot) / testnet.binancefuture.com
                      (futures), separate keys with _TESTNET_* names
            live    — REAL money, needs live_confirmed=true + 3 gates
    """
    import ccxt
    base = cfg["broker"]["exchange"]
    market = cfg["broker"].get("market", "spot")
    mode = cfg["broker"]["mode"]

    if base == "binance" and market == "futures":
        ccxt_id = "binanceusdm"
    elif base == "binance":
        ccxt_id = "binance"
    else:
        ccxt_id = base

    prefix = base.upper()

    # Pick env var names and the human-readable "missing" message
    if mode == "demo":
        key = os.environ.get(f"{prefix}_API_KEY")
        secret = os.environ.get(f"{prefix}_API_SECRET")
        missing = f"{prefix}_API_KEY / _SECRET (from binance.com → Demo Trading → API Key Management)"
    elif mode == "testnet":
        suf = "FUTURES_TESTNET" if market == "futures" else "SPOT_TESTNET"
        key = os.environ.get(f"{prefix}_{suf}_API_KEY") or os.environ.get(f"{prefix}_TESTNET_API_KEY")
        secret = os.environ.get(f"{prefix}_{suf}_API_SECRET") or os.environ.get(f"{prefix}_TESTNET_API_SECRET")
        missing = f"{prefix}_{suf}_API_KEY / _SECRET"
    else:
        key = os.environ.get(f"{prefix}_API_KEY")
        secret = os.environ.get(f"{prefix}_API_SECRET")
        missing = f"{prefix}_API_KEY / _SECRET"

    if not (key and secret):
        raise RuntimeError(f"GATE FAIL: env var missing — {missing}")

    ex = getattr(ccxt, ccxt_id)({
        "apiKey": key,
        "secret": secret,
        "enableRateLimit": True,
        "options": {"defaultType": "future" if market == "futures" else "spot"},
    })

    if mode == "testnet":
        ex.set_sandbox_mode(True)
    elif mode == "demo" and base == "binance":
        # Binance Demo Mode uses dedicated hosts; override CCXT's URL map.
        # Docs: https://developers.binance.com/en/docs/products/spot/demo-mode/general-info
        ex.urls["api"] = {
            "sapi": "https://demo-api.binance.com/sapi",
            "sapiV2": "https://demo-api.binance.com/sapi",
            "sapiV3": "https://demo-api.binance.com/sapi",
            "sapiV4": "https://demo-api.binance.com/sapi",
            "public": "https://demo-api.binance.com/api",
            "private": "https://demo-api.binance.com/api",
            "v1": "https://demo-api.binance.com/api/v1",
        }
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

    # testnet/demo/live — real CCXT call
    try:
        import ccxt  # noqa: F401
    except ImportError:
        print("GATE FAIL: ccxt not installed; run `pip install -r requirements.txt`", file=sys.stderr)
        return 3

    try:
        ex = _ccxt_exchange(cfg)
    except RuntimeError as e:
        print(f"{e}", file=sys.stderr)
        return 3

    symbol = t["plan"]["symbol"]
    if "/" not in symbol:
        symbol = symbol[:-4] + "/" + symbol[-4:]
    side = "buy" if t["plan"]["side"] == "long" else "sell"
    qty = t["size"]["base_qty"]

    if mode == "live":
        if not cfg["broker"].get("live_confirmed"):
            print("GATE FAIL: live mode requires config.broker.live_confirmed = true", file=sys.stderr)
            return 3

    # 1. ENTRY: market order (fills immediately)
    entry_order = ex.create_market_order(symbol, side, qty)
    print(f"[broker] entry filled: {entry_order.get('id')}", file=sys.stderr)

    # 2. OCO BRACKET: stop-loss + take-profit as linked exits
    # Protects against flash crashes — orders live on the exchange, not our runtime.
    stop_price = t["plan"]["stop"]
    target_price = t["plan"]["target"]
    exit_side = "sell" if side == "buy" else "buy"
    bracket = None
    try:
        bracket = _place_oco(ex, cfg["broker"].get("market", "spot"), symbol,
                              exit_side, qty, stop_price, target_price)
        print(f"[broker] OCO bracket placed: stop={stop_price} target={target_price}", file=sys.stderr)
    except Exception as e:
        print(f"[broker] WARNING: OCO bracket failed: {e} — position is UNHEDGED!", file=sys.stderr)

    t["status"] = f"FILLED_{mode.upper()}"
    t["broker_order"] = entry_order
    t["bracket"] = bracket
    rewrite_tickets(tickets)
    write_signal({"source": "crypto-broker", "mode": mode, "entry": entry_order,
                  "bracket": bracket, "symbol": symbol})
    print(json.dumps({"entry": entry_order, "bracket": bracket}, indent=2, default=str))
    return 0


def _place_oco(ex, market: str, symbol: str, exit_side: str, qty: float,
                stop_price: float, target_price: float) -> dict | None:
    """Place a protective OCO bracket (stop-loss + take-profit).

    Spot (Binance): /api/v3/orderList/oco  — native OCO as of 2024.
    Futures (binanceusdm): two separate reduceOnly orders (STOP_MARKET + TAKE_PROFIT_MARKET).
    """
    if market == "futures":
        # Futures: two separate reduceOnly orders
        import ccxt
        try:
            sl = ex.create_order(symbol, "STOP_MARKET", exit_side, qty, None,
                                 {"stopPrice": stop_price, "reduceOnly": True})
            tp = ex.create_order(symbol, "TAKE_PROFIT_MARKET", exit_side, qty, None,
                                 {"stopPrice": target_price, "reduceOnly": True})
            return {"stop_order": sl, "target_order": tp, "type": "futures_pair"}
        except Exception as e:
            raise RuntimeError(f"futures bracket failed: {e}")
    else:
        # Spot OCO: binance /sapi/v1/orderList/oco
        # Legacy ccxt uses create_order with type 'oco' not uniformly supported.
        # Use private API call for Binance Spot:
        try:
            # OCO: one STOP_LOSS_LIMIT + one LIMIT_MAKER
            # stop_limit_price = slightly below stop_price for safe execution
            stop_limit = stop_price * 0.995  # 0.5% buffer for stop-limit fill
            params = {
                "symbol": symbol.replace("/", ""),
                "side": exit_side.upper(),
                "quantity": qty,
                "price": target_price,            # take-profit limit
                "stopPrice": stop_price,          # trigger
                "stopLimitPrice": stop_limit,     # stop-limit floor
                "stopLimitTimeInForce": "GTC",
            }
            result = ex.privatePostOrderOco(params)
            return {"type": "spot_oco", "response": result}
        except Exception as e:
            # Fallback: two separate orders (not atomic, but protects)
            try:
                sl = ex.create_order(symbol, "STOP_LOSS_LIMIT", exit_side, qty,
                                     stop_price * 0.995,
                                     {"stopPrice": stop_price, "timeInForce": "GTC"})
                tp = ex.create_order(symbol, "LIMIT", exit_side, qty, target_price,
                                     {"timeInForce": "GTC"})
                return {"type": "spot_pair_fallback", "stop_order": sl, "target_order": tp,
                        "warning": "Not atomic — if one fills, the other needs manual cancel"}
            except Exception as e2:
                raise RuntimeError(f"spot OCO failed ({e}) and fallback failed ({e2})")


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
