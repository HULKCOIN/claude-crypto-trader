#!/usr/bin/env python3
"""Append-only JSONL signal queue + reader.

The 11 Claude skills write their output here as one JSON object per line.
External agents (Hermes, GPT, Gemini, custom executors) poll this file.

Usage from Python:
    from bridge.signal_queue import SignalQueue
    q = SignalQueue()
    for signal in q.tail():
        print(signal)

Usage from CLI:
    python3 bridge/signal_queue.py tail                   # follow new signals
    python3 bridge/signal_queue.py since 2026-10-23T06    # since timestamp
    python3 bridge/signal_queue.py filter --source crypto-scanner --symbol BTCUSDT
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Iterator


QUEUE_PATH = Path(__file__).resolve().parent / "signal_queue.jsonl"


class SignalQueue:
    def __init__(self, path: Path = QUEUE_PATH):
        self.path = path

    def all(self) -> list[dict]:
        if not self.path.exists():
            return []
        return [json.loads(l) for l in self.path.read_text().splitlines() if l.strip()]

    def filter(self, **kwargs) -> list[dict]:
        return [s for s in self.all() if all(s.get(k) == v for k, v in kwargs.items())]

    def since(self, iso_ts: str) -> list[dict]:
        return [s for s in self.all() if s.get("ts", "") >= iso_ts]

    def tail(self, poll_interval: float = 2.0) -> Iterator[dict]:
        """Yield new signals as they are appended. Blocks forever."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.touch(exist_ok=True)
        pos = self.path.stat().st_size
        while True:
            size = self.path.stat().st_size
            if size > pos:
                with open(self.path) as f:
                    f.seek(pos)
                    for line in f:
                        line = line.strip()
                        if line:
                            try:
                                yield json.loads(line)
                            except json.JSONDecodeError:
                                continue
                pos = size
            time.sleep(poll_interval)


def main() -> int:
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("tail")
    pt = sub.add_parser("since"); pt.add_argument("ts")
    pf = sub.add_parser("filter")
    pf.add_argument("--source"); pf.add_argument("--symbol"); pf.add_argument("--verdict")
    args = p.parse_args()

    q = SignalQueue()
    if args.cmd == "tail":
        for sig in q.tail():
            print(json.dumps(sig))
    elif args.cmd == "since":
        for sig in q.since(args.ts):
            print(json.dumps(sig))
    elif args.cmd == "filter":
        kwargs = {k: v for k, v in {"source": args.source, "symbol": args.symbol,
                                     "verdict": args.verdict}.items() if v}
        for sig in q.filter(**kwargs):
            print(json.dumps(sig))
    return 0


if __name__ == "__main__":
    sys.exit(main())
