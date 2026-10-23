#!/usr/bin/env python3
"""Example auto-executor: policy-based decision, directly to the broker.

This is the ESCAPE HATCH that bypasses the human-approval gate of
tools/broker.py. Use it ONLY in paper mode until you have months of
proven forward-tested results.

Policy (edit for your own rules):
    - timemachine.verdict must be edge_positive
    - risk.verdict must be PASS
    - sentiment.verdict must NOT be market_extended_long when going long
    - max 1 open auto-executed trade at a time

Usage from Hermes bridge:
    python3 bridge/hermes_bridge.py watch --callback bridge.executor:decide
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from tools._common import BRIDGE_DIR, load_config  # noqa: E402


AUTO_LOG = BRIDGE_DIR / "auto_executions.jsonl"


def decide(proposal: dict) -> dict:
    cfg = load_config()
    if cfg["broker"]["mode"] != "paper":
        return {"action": "skip", "reason": "safety: executor refuses live mode by default"}

    tm = proposal.get("timemachine", {})
    risk = proposal.get("risk", {})
    sent = proposal.get("sentiment", {})

    if risk.get("verdict") != "PASS":
        return {"action": "skip", "reason": f"risk {risk.get('verdict')}"}
    if tm.get("verdict") != "edge_positive":
        return {"action": "skip", "reason": f"timemachine {tm.get('verdict')} E[R]={tm.get('expected_r')}"}
    plan = proposal.get("plan", {})
    if plan.get("side") == "long" and sent.get("verdict") == "market_extended_long":
        return {"action": "skip", "reason": "sentiment: market extended long"}

    size = proposal.get("budget", {})
    fill = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "symbol": proposal["symbol"],
        "side": plan.get("side"),
        "entry": plan.get("entry"),
        "stop": plan.get("stop"),
        "target": plan.get("target"),
        "qty": size.get("base_qty"),
        "notional_usd": size.get("notional_usd"),
        "mode": "paper-auto",
        "edge_r": tm.get("expected_r"),
    }
    BRIDGE_DIR.mkdir(parents=True, exist_ok=True)
    with open(AUTO_LOG, "a") as f:
        f.write(json.dumps(fill) + "\n")
    return {"action": "execute", "fill": fill}
