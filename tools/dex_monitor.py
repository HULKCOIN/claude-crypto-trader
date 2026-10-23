#!/usr/bin/env python3
"""crypto-dex: Dexscreener monitor für neue Memecoin-Pools.

Dexscreener API ist komplett kostenlos, kein Key:
  - 300 req/min für Paare/Token/Search
  - 60 req/min für Profile/Boosts

Endpoints wir nutzen:
  - GET /token-profiles/latest/v1 → brandneue Token-Profile
  - GET /latest/dex/search?q=<pair> → Trending-/Volumen-basierte Suche

Signal-Logik:
  - Nur Pools mit >=24h Alter aber <=7 Tage (nicht ganz frisch, nicht alt)
  - Mindest-Liquidität $50k (Rug-Schutz, konfigurierbar)
  - Mindest-24h-Volumen $100k
  - Mindest-Price-Change-24h: +20% oder -20% (bewegt sich)
Signal landet im signal_queue und wird von push_signals übernommen.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import BRIDGE_DIR, http_get, iso_now, write_signal  # noqa: E402


DEX_PROFILES = "https://api.dexscreener.com/token-profiles/latest/v1"
DEX_SEARCH = "https://api.dexscreener.com/latest/dex/search"
DEX_PAIR_FMT = "https://api.dexscreener.com/latest/dex/tokens/{address}"

# Dedup: welche Pool-Adressen wir in dieser Runde schon gepostet haben
SEEN_FILE = BRIDGE_DIR / "dex_seen.json"
SEEN_SNAPSHOT = Path(__file__).resolve().parent.parent / "data" / "snapshots" / "dex_seen.json"
SEEN_TTL_HOURS = 48


def _load_seen() -> dict:
    # Restore from CI snapshot if local is missing (fresh runner each time)
    if not SEEN_FILE.exists() and SEEN_SNAPSHOT.exists():
        try:
            BRIDGE_DIR.mkdir(parents=True, exist_ok=True)
            SEEN_FILE.write_text(SEEN_SNAPSHOT.read_text())
        except Exception:
            pass
    try:
        if SEEN_FILE.exists():
            d = json.loads(SEEN_FILE.read_text())
            # Expire old entries
            import time as _t
            cutoff = _t.time() - SEEN_TTL_HOURS * 3600
            return {k: v for k, v in d.items() if v > cutoff}
    except Exception:
        pass
    return {}


def _save_seen(d: dict) -> None:
    try:
        BRIDGE_DIR.mkdir(parents=True, exist_ok=True)
        SEEN_FILE.write_text(json.dumps(d))
    except Exception as e:
        print(f"[dex] seen save: {e}", file=sys.stderr)


def fetch_profiles(limit: int = 30) -> list[dict]:
    """Neueste Token-Profile (dies ist i.d.R. neu-gelistete Projekte)."""
    try:
        data = http_get(DEX_PROFILES)
    except Exception as e:
        print(f"[dex] profiles: {e}", file=sys.stderr)
        return []
    if isinstance(data, list):
        return data[:limit]
    if isinstance(data, dict):
        return (data.get("profiles") or data.get("data") or [])[:limit]
    return []


def fetch_pair_details(chain: str, token_addr: str) -> dict | None:
    """Hole Live-Liquidität/Volumen/Price-Change für ein Token."""
    try:
        url = DEX_PAIR_FMT.format(address=token_addr)
        data = http_get(url)
        pairs = data.get("pairs") or []
        if not pairs:
            return None
        # Beste Paar nach Liquidität
        best = max(pairs, key=lambda p: float((p.get("liquidity") or {}).get("usd") or 0))
        return best
    except Exception:
        return None


def analyze_profile(prof: dict, seen: dict, min_liq: float, min_vol: float, min_change: float) -> dict | None:
    """Prüft ein Profile, hole Live-Daten, erzeuge Signal falls Trigger stimmt."""
    import time as _t

    chain = prof.get("chainId") or prof.get("chain") or ""
    token_addr = prof.get("tokenAddress") or prof.get("address") or ""
    if not chain or not token_addr:
        return None

    key = f"{chain}:{token_addr}".lower()
    if key in seen:
        return None  # bereits gepostet

    pair = fetch_pair_details(chain, token_addr)
    if not pair:
        return None

    liq = float((pair.get("liquidity") or {}).get("usd") or 0)
    vol_24h = float((pair.get("volume") or {}).get("h24") or 0)
    change_24h = float((pair.get("priceChange") or {}).get("h24") or 0)
    price_usd = float(pair.get("priceUsd") or 0)
    pair_created_at = pair.get("pairCreatedAt") or 0

    # Alter: >= 24h aber <= 7 Tage
    age_hours = (int(_t.time() * 1000) - pair_created_at) / 3600000 if pair_created_at else 0
    if age_hours < 24 or age_hours > 24 * 7:
        return None

    # Filter
    if liq < min_liq or vol_24h < min_vol:
        return None
    if abs(change_24h) < min_change:
        return None

    base_sym = (pair.get("baseToken") or {}).get("symbol") or "?"
    base_name = (pair.get("baseToken") or {}).get("name") or ""
    dex_name = pair.get("dexId") or "?"
    pair_url = pair.get("url") or ""
    pair_addr = pair.get("pairAddress") or ""

    # Richtung
    direction = "pump" if change_24h > 0 else "dump"
    signal = "dex_new_mover"

    # Rug-Risk-Score: liq/vol Ratio (höher = besser), + Alter
    rug_score = min(100, int((liq / max(vol_24h, 1)) * 100 + age_hours))

    row = {
        "source": "crypto-dex",
        "chain": chain,
        "symbol": base_sym,
        "name": base_name,
        "signal": signal,
        "direction": direction,
        "price_usd": price_usd,
        "liquidity_usd": round(liq, 0),
        "volume_24h_usd": round(vol_24h, 0),
        "price_change_24h": round(change_24h, 1),
        "age_hours": round(age_hours, 1),
        "dex": dex_name,
        "pair_address": pair_addr,
        "url": pair_url,
        "rug_risk_score": rug_score,
        "note": f"{base_sym} auf {chain}/{dex_name}: {change_24h:+.1f}% in 24h, Liq ${liq/1000:.0f}k, Vol ${vol_24h/1000:.0f}k",
        "ts": iso_now(),
    }

    seen[key] = _t.time()
    return row


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--min-liq", type=float, default=50_000.0)
    p.add_argument("--min-vol", type=float, default=100_000.0)
    p.add_argument("--min-change", type=float, default=20.0, help="Min abs 24h %")
    p.add_argument("--limit", type=int, default=30)
    args = p.parse_args()

    profiles = fetch_profiles(limit=args.limit)
    if not profiles:
        print("[dex] no profiles returned", file=sys.stderr)
        print("[]")
        return 0

    seen = _load_seen()
    out = []
    for prof in profiles:
        try:
            row = analyze_profile(prof, seen, args.min_liq, args.min_vol, args.min_change)
            if row:
                write_signal(row)
                out.append(row)
        except Exception as e:
            print(f"[dex] analyze error: {e}", file=sys.stderr)
            continue

    _save_seen(seen)
    print(json.dumps(out, indent=2))
    print(f"[dex] {len(out)} new mover(s) found from {len(profiles)} profiles", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
