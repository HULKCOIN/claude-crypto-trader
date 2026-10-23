#!/usr/bin/env python3
"""crypto-risk: gatekeeper. PASS/FAIL every proposed plan against account-wide limits."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path

from _common import BRIDGE_DIR, load_config, write_signal


RISK_LOG = BRIDGE_DIR / "risk_decisions.jsonl"
POSITIONS = BRIDGE_DIR / "positions.json"


def plan_fingerprint(plan: dict) -> str:
    raw = f"{plan['symbol']}_{plan['side']}_{plan['entry']}_{plan['stop']}_{plan['target']}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def load_positions() -> dict:
    if POSITIONS.exists():
        return json.loads(POSITIONS.read_text())
    return {"open": [], "realized_pnl_today": 0.0, "equity": None}


def today_realized_loss_pct(pos: dict, equity: float) -> float:
    pnl = pos.get("realized_pnl_today", 0.0)
    return pnl / equity * 100 if equity else 0


def main() -> int:
    cfg = load_config()
    p = argparse.ArgumentParser()
    p.add_argument("--plan", required=True)
    args = p.parse_args()

    plan = json.loads(Path(args.plan).read_text())
    positions = load_positions()
    equity = positions.get("equity") or cfg["budget"]["account_equity_usd"]

    checks = []
    verdict = "PASS"

    daily = today_realized_loss_pct(positions, equity)
    checks.append({"rule": "daily_loss", "status": "pass" if daily >= -cfg["risk"]["max_daily_loss_pct"] else "fail",
                   "value": round(daily, 2), "limit": -cfg["risk"]["max_daily_loss_pct"]})

    open_n = len(positions.get("open", []))
    checks.append({"rule": "open_positions", "status": "pass" if open_n < cfg["risk"]["max_open_positions"] else "fail",
                   "value": open_n, "limit": cfg["risk"]["max_open_positions"]})

    for c in checks:
        if c["status"] == "fail":
            verdict = "FAIL"
            break

    fp = plan_fingerprint(plan)
    result = {
        "source": "crypto-risk",
        "verdict": verdict,
        "fingerprint": fp,
        "symbol": plan["symbol"],
        "checks": checks,
    }

    BRIDGE_DIR.mkdir(parents=True, exist_ok=True)
    with open(RISK_LOG, "a") as f:
        f.write(json.dumps(result) + "\n")
    write_signal(result)
    print(json.dumps(result, indent=2))
    return 0 if verdict == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
