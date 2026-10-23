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
ETHERSCAN_API = "https://api.etherscan.io/v2/api"
BSCSCAN_API = "https://api.etherscan.io/v2/api"   # V2 nutzt chainid=56 für BSC
ETH_CHAIN_ID = 1  # mainnet
BSC_CHAIN_ID = 56
BTC_PRICE_API = "https://api.coingecko.com/api/v3/simple/price?ids=bitcoin,ethereum,binancecoin,solana&vs_currencies=usd"
SOLSCAN_API = "https://public-api.solscan.io/account/transactions"


def fetch_prices() -> dict:
    """Returns {'btc': X, 'eth': Y, 'bnb': Z, 'sol': W}."""
    try:
        d = http_get(BTC_PRICE_API)
        return {
            "btc": float(d["bitcoin"]["usd"]),
            "eth": float(d["ethereum"]["usd"]),
            "bnb": float(d.get("binancecoin", {}).get("usd", 600)),
            "sol": float(d.get("solana", {}).get("usd", 180)),
        }
    except Exception:
        return {"btc": 68000, "eth": 3500, "bnb": 600, "sol": 180}


def fetch_btc_eth_price() -> tuple[float, float]:
    """Legacy wrapper für Rückwärtskompatibilität."""
    p = fetch_prices()
    return p["btc"], p["eth"]


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


# Bekannte Ethereum-Wallet-Labels (Hot Wallets, Treasuries, DeFi)
EXCHANGE_LABELS = {
    # Binance
    "0x28c6c06298d514db089934071355e5743bf21d60": "Binance 14",
    "0xdfd5293d8e347dfe59e90efd55b2956a1343963d": "Binance 7",
    "0x56eddb7aa87536c09ccc2793473599fd21a8b17f": "Binance 8",
    "0x9696f59e4d72e237be84ffd425dcad154bf96976": "Binance Deposit",
    "0x4976a4a02f38326660d17bf34b431dc6e2eb2327": "Binance Cold",
    # Coinbase
    "0xa910f92acdaf488fa6ef02174fb86208ad7722ba": "Coinbase 10",
    "0x71660c4005ba85c37ccec55d0c4493e66fe775d3": "Coinbase 1",
    "0x503828976d22510aad0201ac7ec88293211d23da": "Coinbase 2",
    # Kraken
    "0x267be1c1d684f78cb4f6a176c4911b741e4ffdc0": "Kraken 4",
    "0xae2d4617c862309a3d75a0ffb358c7a5009c673f": "Kraken 10",
    # OKX
    "0x5041ed759dd4afc3a72b8192c143f72f4724081a": "OKX",
    # Bitfinex
    "0x1151314c646ce4e0efd76d1af4760ae66a9fe30f": "Bitfinex",
    # Known Treasuries / Smart Money
    "0x5a52e96bacdabb82fd05763e25335261b270efcb": "Tether Treasury",
    "0x5754284f345afc66a98fbb0a0afe71e0f007b949": "Tether Treasury",
    "0x742d35cc6634c0532925a3b844bc9e7595f0beb3": "Bitfinex Treasury",
    "0x8488c316e23504b8554e4bdb408d2f08b5bbd36f": "Jump Trading",
    # Memecoin treasuries (examples)
    "0x6cc5f688a315f3dc28a7781717a9a798a59fda7b": "OKX Hot",
}

CHAIN_LABELS = {
    "ethereum": "ETH",
    "bitcoin": "BTC",
    "binance-smart-chain": "BSC",
}


def eth_whale_transfers(min_usd: float, eth_price: float, api_key: str | None) -> list[dict]:
    if not api_key:
        return []  # Etherscan needs a key, but free & instant to get
    params = {
        "chainid": ETH_CHAIN_ID,
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
        frm_addr = (tx.get("from") or "").lower()
        to_addr = (tx.get("to") or "").lower()
        frm_label = EXCHANGE_LABELS.get(frm_addr, "Unknown Wallet")
        to_label = EXCHANGE_LABELS.get(to_addr, "Unknown Wallet")
        # Richtung bestimmen
        is_exchange_frm = any(x in frm_label for x in ("Binance", "Coinbase", "Kraken", "OKX", "Bitfinex"))
        is_exchange_to = any(x in to_label for x in ("Binance", "Coinbase", "Kraken", "OKX", "Bitfinex"))
        if is_exchange_to and not is_exchange_frm:
            signal = "potential_sell_pressure"
        elif is_exchange_frm and not is_exchange_to:
            signal = "potential_accumulation"
        elif is_exchange_frm and is_exchange_to:
            signal = "exchange_to_exchange"
        else:
            signal = "wallet_to_wallet"
        out.append({
            "source": "crypto-onchain",
            "chain": "ethereum",
            "symbol": "ETH",
            "amount_eth": round(value_eth, 2),
            "amount_usd": round(amount_usd, 0),
            "from_addr": frm_addr,
            "to_addr": to_addr,
            "from_label": frm_label,
            "to_label": to_label,
            "signal": signal,
            "tx_hash": tx.get("hash"),
            "ts": iso_now(),
        })
    return out




def bsc_whale_transfers(min_usd: float, bnb_price: float, api_key: str | None) -> list[dict]:
    """BNB Smart Chain whales via Etherscan V2 (chainid=56).
    Watchen die ersten paar bekannten Binance Hot Wallets auf BSC."""
    if not api_key:
        return []
    bsc_hot_wallets = [
        "0x8894e0a0c962cb723c1976a4421c95949be2d4e3",  # Binance Hot Wallet
        "0xf977814e90da44bfa03b6295a0616a897441acec",  # Binance 8
    ]
    out = []
    for addr in bsc_hot_wallets:
        params = {
            "chainid": BSC_CHAIN_ID, "module": "account", "action": "txlist",
            "address": addr, "startblock": 0, "endblock": 99_999_999,
            "page": 1, "offset": 10, "sort": "desc", "apikey": api_key,
        }
        try:
            data = http_get(BSCSCAN_API, params)
        except Exception as e:
            print(f"[onchain] bsc {addr[:10]}: {e}", file=sys.stderr)
            continue
        result = data.get("result")
        if not isinstance(result, list):
            continue
        for tx in result[:5]:
            if not isinstance(tx, dict):
                continue
            value_bnb = int(tx.get("value", "0") or "0") / 1e18
            amount_usd = value_bnb * bnb_price
            if amount_usd < min_usd:
                continue
            out.append({
                "source": "crypto-onchain",
                "chain": "bsc",
                "symbol": "BNB",
                "amount_bnb": round(value_bnb, 2),
                "amount_usd": round(amount_usd, 0),
                "from_addr": tx.get("from", "").lower(),
                "to_addr": tx.get("to", "").lower(),
                "signal": "large_bnb_transfer",
                "tx_hash": tx.get("hash"),
                "ts": iso_now(),
            })
    return out


# Whale-Pattern-Tracker: wenn gleiches Wallet 2+ große TX in 24h → Signal
def detect_repeat_whale_pattern(all_transfers: list[dict]) -> list[dict]:
    """Erkennt Wallets die mehrere große Transfers in kurzer Zeit haben."""
    import time as _t
    from collections import defaultdict
    by_wallet = defaultdict(list)
    now = _t.time()
    for tx in all_transfers:
        wallet = tx.get("from_addr") or tx.get("from") or ""
        if not wallet:
            continue
        by_wallet[wallet].append(tx)

    patterns = []
    for wallet, txs in by_wallet.items():
        if len(txs) >= 2:
            total_usd = sum(t.get("amount_usd", 0) or 0 for t in txs)
            chains = set(t.get("chain", "?") for t in txs)
            patterns.append({
                "source": "crypto-onchain",
                "signal": "repeat_whale_activity",
                "chain": "+".join(chains),
                "symbol": "MIXED",
                "wallet": wallet[:16] + "...",
                "tx_count": len(txs),
                "amount_usd": round(total_usd, 0),
                "note": f"Wallet {wallet[:10]}... hat {len(txs)} TX in dieser Runde = Smart-Money-Aktivität",
                "ts": iso_now(),
            })
    return patterns


def main() -> int:
    cfg = load_config()
    p = argparse.ArgumentParser()
    p.add_argument("--symbol", help="filter: BTC, ETH, BNB, SOL")
    p.add_argument("--min-usd", type=float, default=cfg["onchain"]["min_transfer_usd"])
    args = p.parse_args()

    prices = fetch_prices()
    etherscan_key = os.environ.get("ETHERSCAN_API_KEY")

    out = []
    if not args.symbol or args.symbol.upper() == "BTC":
        out += btc_whale_transfers(args.min_usd, prices["btc"])
    if not args.symbol or args.symbol.upper() == "ETH":
        out += eth_whale_transfers(args.min_usd, prices["eth"], etherscan_key)
    if not args.symbol or args.symbol.upper() == "BNB":
        out += bsc_whale_transfers(args.min_usd, prices["bnb"], etherscan_key)
    # SOL skipped für jetzt — Solscan Public API changed, braucht Pro

    # Pattern-Detection
    patterns = detect_repeat_whale_pattern(out)
    out += patterns

    for row in out:
        write_signal(row)
    print(json.dumps(out, indent=2))
    print(f"[onchain] {len(out)} whale events (davon {len(patterns)} Repeat-Pattern)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
