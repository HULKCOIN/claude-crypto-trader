#!/usr/bin/env python3
"""crypto-newsbot-setup: Registriert Bot-Commands + postet Info-Nachricht + Welcome.

Was es tut (einmalig nach Deployment ausführen):
  1. Registriert alle Bot-Commands via setMyCommands → erscheinen im "/"-Menü
  2. Setzt die Bot-Description (sichtbar auf Bot-Profil)
  3. Postet eine Info-Nachricht in die News-Gruppe (manuell anpinnen!)
  4. Setzt den Group-"About"-Text falls Admin-Rechte

Env-Vars:
  TELEGRAM_NEWS_BOT_TOKEN  — Bot-Token
  TELEGRAM_NEWS_CHAT_ID    — -1004382218113

Aufruf:
  python3 tools/newsbot_setup.py           — alles setzen
  python3 tools/newsbot_setup.py --commands-only
  python3 tools/newsbot_setup.py --post-info
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from urllib.parse import urlencode
from urllib.request import Request, urlopen


COMMANDS = [
    {"command": "help", "description": "Alle Befehle anzeigen"},
    {"command": "news", "description": "📰 Letzte 10 News-Items"},
    {"command": "whales", "description": "🐋 Letzte Großtransfers (BTC/ETH/BSC)"},
    {"command": "sentiment", "description": "📊 Fear & Greed + Funding Rates"},
    {"command": "dex", "description": "🚀 Neue Memecoin-Mover (Dexscreener)"},
    {"command": "scan", "description": "🔍 Scanner jetzt starten"},
    {"command": "signals", "description": "📝 Letzte 10 Signale aus Queue"},
    {"command": "status", "description": "🎯 Trading-Bot Status"},
    {"command": "research", "description": "📚 Tiefere Research zu einem Topic"},
    {"command": "start", "description": "Begrüßung + Hilfe"},
]

BOT_DESCRIPTION = (
    "Crypto Signals Bot — Live-News, Whale-Alerts, "
    "Sentiment-Analyse und automatische Trading-Signale für Crypto. "
    "Nutze /help für alle Befehle."
)

BOT_SHORT_DESCRIPTION = (
    "📊 Live-Crypto-Signals: News, Whales, Dex-Mover, Setups. "
    "Komplett automatisiert, Paper-Trading. DYOR."
)

INFO_MESSAGE = """<b>🚀 Crypto Signals — Info</b>

Willkommen in der offiziellen Signal-Gruppe. Hier bekommst du alle 5 Minuten eine Lage-Übersicht aus einem vollautomatisierten Trading-System.

<b>Was läuft hier:</b>
📰 News von CoinDesk + Cointelegraph (deutsch übersetzt)
🐋 Whale-Alerts: BTC, ETH, BNB-Chain Großtransfers
📊 Sentiment: Fear &amp; Greed, Funding, Long/Short-Ratio
🚀 Dex-Mover: neue Memecoin-Pools (Dexscreener)
📈 Trade-Setups: Breakout, Flag, Reclaim MA20, Oversold
🤖 Jedes Signal mit Monte-Carlo-Odds (2000 Simulationen)
💰 Trading im Binance-Demo-Modus (Paper, kein echtes Geld)

<b>Commands:</b>
/help — alle Befehle
/news — letzte News
/whales — Whale-Alerts
/sentiment — Marktstimmung
/dex — neue Memecoin-Mover
/scan — Scanner jetzt starten
/signals — letzte Signale
/status — Bot-Status
/research &lt;topic&gt; — Research-Frage

Oder frag mich einfach direkt: <i>"Was ist mit Solana los?"</i>, <i>"Warum fällt BTC?"</i> — ich recherchiere und antworte auf Deutsch.

<b>Regeln:</b>
❌ Keine Finanz-Beratung — alle Signale sind nur Entscheidungs-Hilfen.
❌ Risikiere nie mehr als du verlieren kannst.
❌ DYOR (Do Your Own Research) ist Pflicht.
❌ Kein Shilling, keine Scam-Links, keine Memecoin-Pushes.

<b>Technik:</b>
Open Source auf GitHub.
Läuft 24/7 via Cron.
Backend: Yahoo Finance, Coinbase, Etherscan V2, Dexscreener, CoinGecko.
"""


def _token() -> str:
    tok = os.environ.get("TELEGRAM_NEWS_BOT_TOKEN") or os.environ.get("TELEGRAM_BOT_TOKEN")
    if not tok:
        print("ERROR: TELEGRAM_NEWS_BOT_TOKEN missing", file=sys.stderr)
        sys.exit(2)
    return tok


def _chat_id() -> str:
    cid = os.environ.get("TELEGRAM_NEWS_CHAT_ID")
    if not cid:
        print("ERROR: TELEGRAM_NEWS_CHAT_ID missing", file=sys.stderr)
        sys.exit(2)
    return cid


def _tg(method: str, **params) -> dict:
    url = f"https://api.telegram.org/bot{_token()}/{method}"
    data = urlencode(params).encode()
    try:
        with urlopen(Request(url, data=data), timeout=30) as r:
            return json.loads(r.read().decode())
    except Exception as e:
        # Telegram liefert bei Markdown-Fehlern 400 — Body enthält Grund
        try:
            body = e.read().decode() if hasattr(e, "read") else ""  # type: ignore[attr-defined]
            return {"ok": False, "error": str(e), "body": body}
        except Exception:
            return {"ok": False, "error": str(e)}


def set_commands() -> None:
    print("[setup] registriere Bot-Commands...")
    r = _tg("setMyCommands", commands=json.dumps(COMMANDS))
    print(f"[setup] setMyCommands → ok={r.get('ok')} ({len(COMMANDS)} Befehle)")


def set_description() -> None:
    print("[setup] setze Bot-Description...")
    r1 = _tg("setMyDescription", description=BOT_DESCRIPTION)
    print(f"[setup] setMyDescription → ok={r1.get('ok')}")
    r2 = _tg("setMyShortDescription", short_description=BOT_SHORT_DESCRIPTION)
    print(f"[setup] setMyShortDescription → ok={r2.get('ok')}")


def post_info() -> None:
    cid = _chat_id()
    print(f"[setup] poste Info-Nachricht in Chat {cid}...")
    # Erst HTML versuchen (robuster), dann plain als Fallback
    r = _tg("sendMessage", chat_id=cid, text=INFO_MESSAGE[:4000],
            parse_mode="HTML", disable_web_page_preview="true")
    if not r.get("ok"):
        print(f"[setup] HTML-Send fehlgeschlagen: {r.get('error')} body={r.get('body','')[:200]}")
        print("[setup] Fallback: sende als plain-text ohne parse_mode...")
        import re as _re
        plain = _re.sub(r"<[^>]+>", "", INFO_MESSAGE).replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
        r = _tg("sendMessage", chat_id=cid, text=plain[:4000], disable_web_page_preview="true")
    msg_id = (r.get("result") or {}).get("message_id")
    print(f"[setup] sendMessage → ok={r.get('ok')}, msg_id={msg_id}")
    if not msg_id:
        print(f"[setup] FEHLER: {r}")
        return
    # Versuch zu pinnen (klappt nur mit Admin-Rechten)
    pr = _tg("pinChatMessage", chat_id=cid, message_id=msg_id, disable_notification="false")
    print(f"[setup] pinChatMessage → ok={pr.get('ok')} (braucht Admin-Rechte)")
    if not pr.get("ok"):
        print(f"[setup] Pin-Fehler: {pr.get('error') or pr.get('description')}")
        print(f"[setup] → Gib dem Bot Admin-Rechte oder pinne die Nachricht manuell (msg_id={msg_id}).")


def set_group_about() -> None:
    """Setzt Group-Description falls Bot Admin-Rechte hat."""
    about = (
        "Crypto Signals — automatisierte News, Whale-Alerts "
        "und Trade-Setups alle 5 Min. /help für Commands. "
        "Keine Finanz-Beratung, DYOR."
    )
    cid = _chat_id()
    print(f"[setup] setze Chat-Description...")
    try:
        r = _tg("setChatDescription", chat_id=cid, description=about)
        print(f"[setup] setChatDescription → ok={r.get('ok')}")
        if not r.get("ok"):
            print(f"[setup] Beschreibung-Fehler: {r.get('description')}")
            print("[setup] → Gib dem Bot Admin-Rechte für Chat-Edit, oder setze manuell.")
    except Exception as e:
        print(f"[setup] setChatDescription error: {e}")


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--commands-only", action="store_true", help="nur Commands setzen")
    p.add_argument("--post-info", action="store_true", help="nur Info-Message posten")
    p.add_argument("--description-only", action="store_true", help="nur Beschreibungen setzen")
    args = p.parse_args()

    if args.commands_only:
        set_commands()
    elif args.post_info:
        post_info()
    elif args.description_only:
        set_description()
        set_group_about()
    else:
        set_commands()
        set_description()
        set_group_about()
        post_info()

    print("[setup] fertig.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
