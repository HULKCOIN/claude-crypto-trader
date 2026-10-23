#!/usr/bin/env python3
"""crypto-onchain: FREE whale watcher.

Sources (all free, no credit card):
  - Blockchain.com unconfirmed BTC transactions (no key)
  - Etherscan large ETH + ERC-20 transfers (optional ETHERSCAN_API_KEY,
    free tier: 5 req/s, 100k req/day — key signup is free + instant)
"""
from __future__ import annotations

import argparse
import json
import os
import sys

from _common import http_get, iso_now, load_config, write_signal


BLOCKCHAIN_UNCONFIRMED = "https://blockchain.info/unconfirmed-transactions?format=json"
ETHERSCAN_API = "https://api.etherscan.io/api"
BTC_PRICE_API = "https://api.coingecko.com/api/v3/simple/price?ids=bitcoin,ethereum&vs_currencies=usd"


def fetch_btc_eth_price() -> tuple[float, float]:
    try:
        d = http_get(BTC_PRICE_API)
        return float(d["bitcoin"]["usd"]), float(d["ethereum"]["usd"])
    except Exception:
        return 68000.0, 3500.0  # fallback approximations


def btc_whale_transfers(min_usd: float, btc_price: float) -> list[dict]:
    try:
        data = http_get(BLOCKCHAIN_UNCONFIRMED)
    except Exception as e:
        print(f"[onchain] blockchain.info: {e}", file=sys.stderr)
        return []
    out = []
    for tx in data.get("txs", []):
        total_sat = sum(o.get("value", 0) for o in tx.get("out", []))
        total_btc = total_sat / 1e8
        amount_usd = total_btc * btc_price
        if amount_usd < min_usd:
            continue
        out.append({
            "source": "crypto-onchain",
            "chain": "bitcoin",
            "symbol": "BTC",
            "amount_btc": round(total_btc, 4),
            "amount_usd": round(amount_usd, 0),
            "signal": "large_btc_transfer",
            "tx_hash": tx.get("hash"),
            "note": "unconfirmed; not yet mined",
            "ts": iso_now(),
        })
    return sorted(out, key=lambda r: r["amount_usd"], reverse=True)[:20]


# Known Ethereum exchange hot wallets (abbreviated, used to classify flows)
EXCHANGE_LABELS = {
    "0x28c6c06298d514db089934071355e5743bf21d60": "binance",
    "0xdfd5293d8e347dfe59e90efd55b2956a1343963d": "binance",
    "0x56eddb7aa87536c09ccc2793473599fd21a8b17f": "binance",
    "0xa910f92acdaf488fa6ef02174fb86208ad7722ba": "coinbase",
    "0x71660c4005ba85c37ccec55d0c4493e66fe775d3": "coinbase",
    "0x267be1c1d684f78cb4f6a176c4911b741e4ffdc0": "kraken",
}


def eth_whale_transfers(min_usd: float, eth_price: float, api_key: str | None) -> list[dict]:
    if not api_key:
        return []  # Etherscan needs a key, but free & instant to get
    params = {
        "module": "account",
        "action": "txlist",
        "address": list(EXCHANGE_LABELS.keys())[0],  # Binance hot wallet
        "startblock": 0,
        "endblock": 99_999_999,
        "page": 1,
        "offset": 20,
        "sort": "desc",
        "apikey": api_key,
    }
    try:
        data = http_get(ETHERSCAN_API, params)
    except Exception as e:
        print(f"[onchain] etherscan: {e}", file=sys.stderr)
        return []
    result = data.get("result")
    if not isinstance(result, list):
        # Etherscan returns a string like "Invalid API Key" or "NOTOK" on failure
        print(f"[onchain] etherscan error: status={data.get('status')} msg={data.get('message')} result={result!r}", file=sys.stderr)
        return []
    out = []
    for tx in result:
        if not isinstance(tx, dict):
            continue
        value_eth = int(tx.get("value", "0") or "0") / 1e18
        amount_usd = value_eth * eth_price
        if amount_usd < min_usd:
            continue
        frm = (tx.get("from") or "").lower()
        to = (tx.get("to") or "").lower()
        frm_label = EXCHANGE_LABELS.get(frm, "wallet")
        to_label = EXCHANGE_LABELS.get(to, "wallet")
        if to_label in ("binance", "coinbase", "kraken") and frm_label == "wallet":
            signal = "potential_sell_pressure"
        elif frm_label in ("binance", "coinbase", "kraken") and to_label == "wallet":
            signal = "potential_accumulation"
        else:
            signal = "large_eth_transfer"
        out.append({
            "source": "crypto-onchain",
            "chain": "ethereum",
            "symbol": "ETH",
            "amount_eth": round(value_eth, 2),
            "amount_usd": round(amount_usd, 0),
            "from": frm_label,
            "to": to_label,
            "signal": signal,
            "tx_hash": tx.get("hash"),
            "ts": iso_now(),
        })
    return out


def main() -> int:
    cfg = load_config()
    p = argparse.ArgumentParser()
    p.add_argument("--symbol", help="filter: BTC or ETH")
    p.add_argument("--min-usd", type=float, default=cfg["onchain"]["min_transfer_usd"])
    args = p.parse_args()

    btc_price, eth_price = fetch_btc_eth_price()
    etherscan_key = os.environ.get("ETHERSCAN_API_KEY")

    out = []
    if not args.symbol or args.symbol.upper() == "BTC":
        out += btc_whale_transfers(args.min_usd, btc_price)
    if not args.symbol or args.symbol.upper() == "ETH":
        out += eth_whale_transfers(args.min_usd, eth_price, etherscan_key)

    for row in out:
        write_signal(row)
    print(json.dumps(out, indent=2))
    print(f"[onchain] {len(out)} whale transfers > ${args.min_usd:,.0f}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
