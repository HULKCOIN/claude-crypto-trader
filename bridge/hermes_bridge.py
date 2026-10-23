#!/usr/bin/env python3
"""Hermes (or any external AI agent) bridge.

Watches the signal queue, assembles a complete trade proposal (scanner +
exit + timemachine + risk + budget), and hands it to an external agent
callback for the final decide/execute step.

The agent callback is YOUR code — plug in Hermes, GPT, Gemini, a rules
engine, whatever. The default callback just prints the proposal.

Usage:
    # Default (print proposals)
    python3 bridge/hermes_bridge.py watch

    # Point at your own agent
    python3 bridge/hermes_bridge.py watch --callback mymodule:decide

Writing a callback:
    def decide(proposal: dict) -> dict:
        # proposal has: scanner_hit, plan, timemachine, risk, budget
        # return: {"action": "execute" | "skip", "reason": "..."}
        ...
"""
from __future__ import annotations

import argparse
import importlib
import json
import sys
import time
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from bridge.signal_queue import SignalQueue  # noqa: E402


def default_callback(proposal: dict) -> dict:
    tm = proposal.get("timemachine", {})
    risk = proposal.get("risk", {})
    reason = f"P(target)={tm.get('p_target_first')} E[R]={tm.get('expected_r')} risk={risk.get('verdict')}"
    print(json.dumps({"proposal": proposal, "note": reason}, indent=2))
    return {"action": "skip", "reason": "default callback never executes"}


def load_callback(spec: str):
    if ":" not in spec:
        raise ValueError("callback spec must be 'module:function'")
    mod_name, fn_name = spec.split(":", 1)
    mod = importlib.import_module(mod_name)
    return getattr(mod, fn_name)


class ProposalAssembler:
    """Group signals by symbol into complete trade proposals."""

    REQUIRED = {"crypto-scanner", "crypto-exit", "crypto-timemachine", "crypto-risk", "crypto-budget"}

    def __init__(self):
        self._by_symbol: dict[str, dict] = defaultdict(dict)

    def ingest(self, signal: dict) -> dict | None:
        src = signal.get("source")
        sym = signal.get("symbol")
        if not (src and sym):
            return None
        key = {
            "crypto-scanner": "scanner_hit",
            "crypto-exit": "plan",
            "crypto-timemachine": "timemachine",
            "crypto-risk": "risk",
            "crypto-budget": "budget",
            "crypto-news": "news",
            "crypto-sentiment": "sentiment",
            "crypto-onchain": "onchain",
        }.get(src)
        if not key:
            return None
        self._by_symbol[sym][key] = signal
        if self.REQUIRED.issubset({
            "crypto-scanner" if "scanner_hit" in self._by_symbol[sym] else None,
            "crypto-exit" if "plan" in self._by_symbol[sym] else None,
            "crypto-timemachine" if "timemachine" in self._by_symbol[sym] else None,
            "crypto-risk" if "risk" in self._by_symbol[sym] else None,
            "crypto-budget" if "budget" in self._by_symbol[sym] else None,
        }):
            proposal = {"symbol": sym, "assembled_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                        **self._by_symbol[sym]}
            self._by_symbol.pop(sym)
            return proposal
        return None


def main() -> int:
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)
    pw = sub.add_parser("watch")
    pw.add_argument("--callback", default=None, help="module:function (default: print only)")
    pw.add_argument("--poll", type=float, default=2.0)
    args = p.parse_args()

    cb = load_callback(args.callback) if args.callback else default_callback
    assembler = ProposalAssembler()
    q = SignalQueue()

    print(f"[hermes_bridge] watching {q.path}; callback={args.callback or 'default'}", file=sys.stderr)
    for signal in q.tail(poll_interval=args.poll):
        proposal = assembler.ingest(signal)
        if proposal:
            try:
                decision = cb(proposal)
                print(json.dumps({"decision": decision}, default=str), flush=True)
            except Exception as e:
                print(f"[hermes_bridge] callback error: {e}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
