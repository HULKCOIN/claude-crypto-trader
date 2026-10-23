#!/usr/bin/env python3
"""Gemini-Request/Response Logger mit 30-Tage-Rotation + Sanitizer.

Zweck:
- Alle Gemini-Aufrufe werden protokolliert (Debug/Audit)
- Max 30 Tage Daten → danach wird die alte Datei überschrieben
- VOR dem Log + VOR dem Senden werden sensible Daten gefiltert:
  * API-Keys, Bot-Tokens, Chat-IDs
  * Account-Equity, Positions-Beträge
  * Private Wallet-Addressen (eigene)
  * Umgebungs-Variablen jeglicher Art

Benutzung:
  from gemini_log import sanitize, log_call
  prompt = sanitize(prompt)
  log_call("news_bot", prompt, response)
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import BRIDGE_DIR  # noqa: E402


LOG_DIR = Path(__file__).resolve().parent.parent / "data" / "gemini_logs"
RETENTION_DAYS = 30


# Patterns die niemals in Gemini-Requests oder Logs landen dürfen
SENSITIVE_PATTERNS = [
    # API Keys & Tokens — anerkennen am Format
    (re.compile(r"\b\d{9,}:[A-Za-z0-9_-]{30,}\b"), "[REDACTED_TG_TOKEN]"),  # Telegram-Bot-Token
    (re.compile(r"\bAIza[A-Za-z0-9_-]{30,}\b"), "[REDACTED_GOOGLE_KEY]"),   # Google/Gemini
    (re.compile(r"\b[A-Z0-9]{20,}\b"), "[REDACTED_API_KEY]"),               # Binance/Etherscan Style
    (re.compile(r"\bsk-[A-Za-z0-9]{20,}\b"), "[REDACTED_OPENAI_KEY]"),
    # Wallet-Adressen die wir als "eigene" kennzeichnen (User kann Liste erweitern)
    (re.compile(r"\b0x[a-fA-F0-9]{40}\b"), None),  # ETH-Addressen bleiben (öffentlich) außer eigene
    # Private-Keys / Seed-Phrases (12/24 Worte)
    (re.compile(r"\b(?:[a-z]+\s+){11,23}[a-z]+\b"), "[REDACTED_POSSIBLE_SEED]"),
]

# Keys aus Signal-Rows die niemals in Context dürfen
SENSITIVE_KEYS = {
    "account_equity_usd", "balance", "equity", "usdt_balance",
    "position_size", "notional_usd", "dollar_risk",
    "api_key", "api_secret", "token", "secret", "password",
    "private_key", "mnemonic", "seed", "passphrase",
    "chat_id", "bot_token", "user_id", "email",
}

# User-eigene Wallet-Addressen (hier eintragen falls welche in Logs sichtbar werden)
MY_WALLETS: set[str] = set()


def sanitize(text: str) -> str:
    """Entfernt API-Keys, Tokens, Secrets aus einem Text."""
    if not text:
        return text
    out = text
    for pattern, replacement in SENSITIVE_PATTERNS:
        if replacement is None:
            # ETH-Addressen: nur eigene Wallets maskieren
            def _mask(m):
                addr = m.group(0).lower()
                return "[MY_WALLET]" if addr in MY_WALLETS else addr
            out = pattern.sub(_mask, out)
        else:
            out = pattern.sub(replacement, out)
    # Env-Vars nach Namen (jede var die auf _KEY|_TOKEN|_SECRET|_PASSWORD endet)
    for env_key, env_val in os.environ.items():
        upper = env_key.upper()
        if any(upper.endswith(suf) for suf in ("_KEY", "_TOKEN", "_SECRET", "_PASSWORD", "_API_KEY")) \
           and env_val and len(env_val) > 8:
            out = out.replace(env_val, f"[REDACTED_{env_key}]")
    return out


def sanitize_dict(d: dict) -> dict:
    """Entfernt sensible Keys aus einem Dict (für signal-row Kontext)."""
    if not isinstance(d, dict):
        return d
    clean = {}
    for k, v in d.items():
        lk = str(k).lower()
        if lk in SENSITIVE_KEYS or any(s in lk for s in ("secret", "token", "password", "balance", "equity")):
            continue
        if isinstance(v, dict):
            clean[k] = sanitize_dict(v)
        elif isinstance(v, list):
            clean[k] = [sanitize_dict(x) if isinstance(x, dict) else x for x in v]
        elif isinstance(v, str):
            clean[k] = sanitize(v)
        else:
            clean[k] = v
    return clean


def _rotate_file() -> Path:
    """Monatliche Datei → max 1 Monat Daten pro Datei. Danach wird überschrieben."""
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    # Datei pro aktuelles Datum % RETENTION_DAYS → ergibt rotierenden Zyklus
    day = int(time.time() // 86400) % RETENTION_DAYS
    path = LOG_DIR / f"gemini_day{day:02d}.jsonl"
    # Wenn Datei älter als RETENTION_DAYS → leeren (neuer Zyklus)
    if path.exists():
        age_days = (time.time() - path.stat().st_mtime) / 86400
        if age_days > 1:  # letzter Schreibzugriff > 24h her → neuer Zyklus-Tag
            path.write_text("")
    return path


def log_call(caller: str, prompt: str, response: str, meta: dict | None = None) -> None:
    """Loggt einen Gemini-Call. Sanitized BEIDE Felder."""
    try:
        path = _rotate_file()
        row = {
            "ts": int(time.time()),
            "caller": caller,
            "prompt": sanitize(prompt)[:4000],   # cap zur Größenkontrolle
            "response": sanitize(response)[:4000],
            "meta": sanitize_dict(meta or {}),
        }
        with open(path, "a") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    except Exception as e:
        print(f"[gemini_log] write failed: {e}", file=sys.stderr)


def cleanup_old() -> int:
    """Löscht Dateien älter als RETENTION_DAYS. Return: Anzahl gelöschter Dateien."""
    if not LOG_DIR.exists():
        return 0
    cutoff = time.time() - RETENTION_DAYS * 86400
    deleted = 0
    for p in LOG_DIR.glob("gemini_day*.jsonl"):
        try:
            if p.stat().st_mtime < cutoff:
                p.unlink()
                deleted += 1
        except Exception:
            continue
    return deleted


if __name__ == "__main__":
    # Als CLI: Cleanup + Status
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--cleanup", action="store_true")
    p.add_argument("--stats", action="store_true")
    args = p.parse_args()

    if args.cleanup:
        n = cleanup_old()
        print(f"deleted {n} old gemini log files")
    if args.stats or not args.cleanup:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        files = sorted(LOG_DIR.glob("gemini_day*.jsonl"))
        total_lines = 0
        total_bytes = 0
        for f in files:
            lines = sum(1 for _ in f.open())
            total_lines += lines
            total_bytes += f.stat().st_size
            print(f"  {f.name}: {lines} calls, {f.stat().st_size // 1024} KB")
        print(f"\nTotal: {len(files)} files, {total_lines} calls, {total_bytes // 1024} KB")
        print(f"Retention: {RETENTION_DAYS} days")
