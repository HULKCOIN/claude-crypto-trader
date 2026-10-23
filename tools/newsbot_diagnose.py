#!/usr/bin/env python3
"""crypto-newsbot-diagnose: Prüft welchem Bot der Token gehört und welche Chats er kennt.

Env-Vars:
  TELEGRAM_NEWS_BOT_TOKEN  — zu prüfender Token
  TELEGRAM_NEWS_CHAT_ID    — zu prüfende Chat-ID

Output:
  - Bot-Username, First-Name, ID
  - Alle Chats aus den letzten getUpdates
  - Vergleich: ist die Soll-Chat-ID dabei?
"""
from __future__ import annotations

import json
import os
import sys
from urllib.parse import urlencode
from urllib.request import Request, urlopen


def _tg(method: str, token: str, **params) -> dict:
    url = f"https://api.telegram.org/bot{token}/{method}"
    data = urlencode(params).encode()
    try:
        with urlopen(Request(url, data=data), timeout=30) as r:
            return json.loads(r.read().decode())
    except Exception as e:
        try:
            body = e.read().decode() if hasattr(e, "read") else ""  # type: ignore[attr-defined]
            return {"ok": False, "error": str(e), "body": body}
        except Exception:
            return {"ok": False, "error": str(e)}


def main() -> int:
    token = os.environ.get("TELEGRAM_NEWS_BOT_TOKEN") or ""
    want_cid = (os.environ.get("TELEGRAM_NEWS_CHAT_ID") or "").strip()

    if not token:
        print("FEHLER: TELEGRAM_NEWS_BOT_TOKEN nicht gesetzt")
        return 2

    print("=" * 60)
    print("NEWS-BOT TOKEN DIAGNOSE")
    print("=" * 60)
    # Token nur die ersten 10 Zeichen zeigen
    print(f"Token: {token[:10]}... (Länge {len(token)})")
    print(f"Soll-Chat-ID (ENV): {want_cid!r}")
    print()

    # getMe
    me = _tg("getMe", token)
    if not me.get("ok"):
        print(f"FEHLER: getMe → {me}")
        print("→ Token ist ungültig oder abgelaufen.")
        return 1

    bot = me.get("result", {})
    print("Bot-Identität:")
    print(f"  Username : @{bot.get('username')}")
    print(f"  Name     : {bot.get('first_name')}")
    print(f"  ID       : {bot.get('id')}")
    print(f"  can_join : {bot.get('can_join_groups')}")
    print(f"  reads_all: {bot.get('can_read_all_group_messages')}")
    print()

    # getUpdates
    print("Chats aus getUpdates (letzte 100 Updates):")
    updates = _tg("getUpdates", token, limit=100, offset=-100)
    if not updates.get("ok"):
        print(f"  FEHLER: {updates}")
        return 1

    chats = {}
    for u in updates.get("result", []):
        for key in ("message", "edited_message", "channel_post", "my_chat_member", "chat_member"):
            msg = u.get(key)
            if not msg:
                continue
            chat = msg.get("chat") or {}
            cid = str(chat.get("id", ""))
            if not cid:
                continue
            chats[cid] = {
                "title": chat.get("title") or chat.get("username") or chat.get("first_name") or "?",
                "type": chat.get("type"),
            }

    if not chats:
        print("  (keine Chats in den letzten Updates — Bot hat noch nichts empfangen)")
        print("  → Sende eine Testnachricht IN der Gruppe (z.B. '/start@<username>'),")
        print("    damit der Bot die Chat-ID lernt. Oder:")
        print("    Prüfe ob der Bot wirklich in der Gruppe ist und 'Group Privacy' aus hat.")
    else:
        for cid, info in chats.items():
            marker = "  ✅ MATCH" if cid == want_cid else ""
            print(f"  {cid}  type={info['type']:<10}  title={info['title']}{marker}")

    print()
    # Direkttest: getChat auf Soll-ID
    print(f"Direkttest getChat({want_cid}):")
    r = _tg("getChat", token, chat_id=want_cid)
    if r.get("ok"):
        c = r.get("result", {})
        print(f"  ✅ ok: title={c.get('title')} type={c.get('type')} id={c.get('id')}")
        print("  → Chat-ID stimmt, Bot IST drin. Problem muss woanders liegen.")
    else:
        print(f"  ❌ Fehler: {r.get('body') or r.get('error')}")
        print("  → Entweder Bot ist NICHT in diesem Chat, oder Chat-ID ist falsch.")
        print()
        print("Behebung:")
        print("  1. Prüfe TELEGRAM_NEWS_BOT_TOKEN in GitHub Secrets.")
        print("     Soll der Bot sein, der zur News-Gruppe gehört.")
        print(f"  2. Prüfe TELEGRAM_NEWS_CHAT_ID. Aktuell: {want_cid!r}")
        print("     Supergroup-IDs beginnen mit -100... (14 Zeichen inkl. Minus).")
        print("  3. Falls Bot nicht in Gruppe: in Telegram die Gruppe öffnen,")
        print(f"     '@{bot.get('username')}' einladen und als Admin ernennen.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
