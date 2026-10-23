#!/usr/bin/env python3
"""crypto-onchain: large transfer watcher (Whale Alert free tier)."""
from __future__ import annotations

import argparse
import json
import os
import sys

from _common import http_get, load_config, write_signal


WHALE_ALERT = "https://api.whale-alert.io/v1/transactions"


def classify(tx: dict) -> str:
    to = tx.get("to", {}).get("owner_type", "")
    frm = tx.get("from", {}).get("owner_type", "")
    if to == "exchange" and frm != "exchange":
        return "potential_sell_pressure"
    if frm == "exchange" and to != "exchange":
        return "potential_accumulation"
    if tx.get("symbol", "").upper() in ("USDT", "USDC", "DAI") and tx.get("transaction_type") == "mint":
        return "stablecoin_mint_liquidity_in"
    return "neutral"


def main() -> int:
    cfg = load_config()
    p = argparse.ArgumentParser()
    p.add_argument("--symbol")
    p.add_argument("--min-usd", type=float, default=cfg["onchain"]["min_transfer_usd"])
    args = p.parse_args()

    token = os.environ.get(cfg["onchain"]["whale_alert_token_env"])
    if not token:
        print("[onchain] WHALE_ALERT_TOKEN env missing — skipping (free key: https://whale-alert.io)", file=sys.stderr)
        return 0

    params = {"api_key": token, "min_value": args.min_usd}
    if args.symbol:
        params["currency"] = args.symbol.lower()

    try:
        data = http_get(WHALE_ALERT, params)
    except Exception as e:
        print(f"[onchain] {e}", file=sys.stderr)
        return 1

    out = []
    for tx in data.get("transactions", [])[:20]:
        signal = {
            "source": "crypto-onchain",
            "chain": tx.get("blockchain"),
            "symbol": tx.get("symbol"),
            "amount_usd": tx.get("amount_usd"),
            "from": tx.get("from", {}).get("owner_type"),
            "to": tx.get("to", {}).get("owner_type"),
            "signal": classify(tx),
            "tx_hash": tx.get("hash"),
        }
        write_signal(signal)
        out.append(signal)
    print(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
