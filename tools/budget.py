#!/usr/bin/env python3
"""crypto-budget: compute position size for a risk-approved plan."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from _common import load_config, write_signal


def fixed_fractional(equity: float, pct: float, r_per_unit: float, entry: float) -> dict:
    dollar_risk = equity * pct / 100
    base_qty = dollar_risk / r_per_unit if r_per_unit else 0
    notional = base_qty * entry
    return {
        "notional_usd": round(notional, 2),
        "base_qty": round(base_qty, 8),
        "dollar_risk_at_stop": round(dollar_risk, 2),
    }


def main() -> int:
    cfg = load_config()
    p = argparse.ArgumentParser()
    p.add_argument("--plan", required=True)
    p.add_argument("--method", default=cfg["budget"]["sizing_method"], choices=["fixed_fractional"])
    args = p.parse_args()

    plan = json.loads(Path(args.plan).read_text())
    equity = cfg["budget"]["account_equity_usd"]
    pct = cfg["budget"]["risk_per_trade_pct"]

    sizing = fixed_fractional(equity, pct, plan["r_per_unit"], plan["entry"])

    result = {
        "source": "crypto-budget",
        "symbol": plan["symbol"],
        "side": plan["side"],
        "sizing_method": args.method,
        "equity_pct": pct,
        **sizing,
    }
    write_signal(result)
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
