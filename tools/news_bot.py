#!/usr/bin/env python3
"""crypto-newsbot: Interaktiver News- und Research-Bot für Telegram-Gruppe.

Was er kann:
  - Begrüßt neue Mitglieder, erklärt kurz was der Bot kann
  - Beantwortet Fragen auf Deutsch (via Gemini) mit Kontext über das Trading-System
  - /scan /news /sentiment /whales /dex /research <topic> /help
  - Antwortet auf @mentions oder Replies auf seine Nachrichten
  - Fragt bei Aktionen (Scan starten, Trade öffnen) immer vorher nach

Erforderliche Env-Vars:
  TELEGRAM_NEWS_BOT_TOKEN   — eigener Bot für die News-Gruppe
  TELEGRAM_NEWS_CHAT_ID     — -1004382218113
  GEMINI_API_KEY            — für Q&A (optional, fallback: ohne LLM)

Deployment:
  python3 tools/news_bot.py   (long-polling, 24/7 auf GCP-VM via systemd)
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import BRIDGE_DIR, iso_now  # noqa: E402


REPO_ROOT = Path(__file__).resolve().parent.parent
BOT_STATE = BRIDGE_DIR / "news_bot_state.json"

# Welcome-Message für neue Mitglieder
WELCOME_DE = (
    "👋 Willkommen {name} bei *Crypto Signals*!\n\n"
    "Ich bin der News- & Research-Bot. Hier bekommst du:\n"
    "📰 Live-Crypto-News (CoinDesk, Cointelegraph, Trending)\n"
    "🐋 Whale-Alerts (BTC, ETH, BSC Großtransfers)\n"
    "📊 Marktstimmung (Fear & Greed, Funding Rates)\n"
    "🚀 Neue Memecoin-Pools (Dexscreener)\n"
    "📈 Automatische Trade-Signale (Setup, Entry, SL, TP, Odds)\n\n"
    "Schreib `/help` für alle Befehle, oder stell einfach eine Frage — "
    "ich antworte auf Deutsch und kann Research zu Coins, News oder "
    "Trading-Themen durchführen.\n\n"
    "Alle Signale sind *Entscheidungs-Hilfen*, keine Finanz-Beratung. "
    "DYOR und nie mehr riskieren als du verlieren kannst."
)

HELP_DE = (
    "*Crypto News- & Research-Bot*\n\n"
    "📰 */news* — letzte 10 News-Items\n"
    "🐋 */whales* — Großtransfers der letzten Stunde\n"
    "📊 */sentiment* — Fear & Greed + Funding Rates\n"
    "🚀 */dex* — neue Dexscreener-Mover (24h-7d alt)\n"
    "🔍 */scan* — Scanner starten (dauert 1-2 Min)\n"
    "📝 */signals* — letzte 10 Signale aus der Queue\n"
    "🎯 */status* — Trading-Bot Status\n"
    "📚 */research <topic>* — tiefere Analyse via Gemini\n"
    "ℹ️  */help* — diese Hilfe\n\n"
    "💬 Oder frag mich einfach direkt:\n"
    "'Was ist mit Bitcoin los?', 'Warum pumpt Solana?', "
    "'Sollte ich jetzt kaufen?' — ich gebe Research-basierte "
    "Antworten (keine Finanz-Beratung)."
)

# System-Prompt für Gemini: Bot kennt das Trading-System
GEMINI_SYSTEM = """Du bist der Crypto News- & Research-Bot in einer deutschen Telegram-Gruppe.

DEIN KONTEXT:
- Du gehörst zu einem automatisierten Crypto-Trading-System.
- Das System scannt 60+ USDT-Paare auf Yahoo/Coinbase alle 5 Minuten.
- Es nutzt 5 Setup-Typen: breakout_20d, bull_flag, reclaim_ma20, oversold, oversold_bounce.
- Jedes Signal wird durch Monte-Carlo-Simulation (2000 Pfade) und Gemini-Review geprüft.
- Es handelt im Binance Demo-Modus (Paper), nicht mit echtem Geld.
- News kommen von CoinDesk/Cointelegraph RSS + CoinGecko Trending.
- Whale-Tracking: Blockchain.info (BTC), Etherscan V2 (ETH + BSC).
- Dexscreener-Monitor erkennt neue Memecoin-Pools.
- Opening-Range-Breakout läuft parallel für US-/DE-Aktien während Börsenzeiten.

DEIN JOB:
- Beantworte Fragen auf Deutsch, kurz und präzise (max 300 Wörter).
- Nutze Markdown-Formatierung (*fett*, _kursiv_, `code`).
- Bei Trading-Fragen: Gib Research/Fakten, aber KEINE Buy/Sell-Empfehlungen.
- Immer am Ende dazu-sagen: "Keine Finanz-Beratung, DYOR."
- Wenn der User eine Aktion will (Scanner starten, Trade öffnen, Position schließen):
  → antworte mit "ACTION:scan" / "ACTION:open SYMBOL" / "ACTION:close SYMBOL" am Anfang.
- Wenn Research zu einem Coin: nenne Fakten, Trends, bekannte News, Risiken.
- Wenn du etwas nicht weißt: sag das ehrlich, keine Halluzination.

STIL:
- Freundlich, auf Augenhöhe, nicht geschwätzig.
- Keine Finanz-Beratung, immer der Hinweis am Ende.
- Deutsch, auch bei englischen Fach-Begriffen (Setup, Breakout, Entry sind okay).
"""


def _tg_token() -> str:
    tok = os.environ.get("TELEGRAM_NEWS_BOT_TOKEN") or os.environ.get("TELEGRAM_BOT_TOKEN")
    if not tok:
        raise RuntimeError("TELEGRAM_NEWS_BOT_TOKEN missing")
    return tok


def _news_chat_id() -> str | None:
    return os.environ.get("TELEGRAM_NEWS_CHAT_ID")


def _tg(method: str, **params) -> dict:
    url = f"https://api.telegram.org/bot{_tg_token()}/{method}"
    data = urlencode(params).encode()
    with urlopen(Request(url, data=data), timeout=30) as r:
        return json.loads(r.read().decode())


def send(chat_id: int | str, text: str, parse_mode: str = "Markdown", reply_to: int | None = None) -> dict | None:
    try:
        kwargs = {"chat_id": chat_id, "text": text[:4000], "parse_mode": parse_mode, "disable_web_page_preview": "true"}
        if reply_to:
            kwargs["reply_to_message_id"] = reply_to
        return _tg("sendMessage", **kwargs)
    except Exception as e:
        print(f"[newsbot] send failed: {e}", file=sys.stderr)
        # Retry ohne Markdown falls Parse-Fehler
        try:
            kwargs["parse_mode"] = ""
            return _tg("sendMessage", **kwargs)
        except Exception:
            return None


def _load_state() -> dict:
    try:
        if BOT_STATE.exists():
            return json.loads(BOT_STATE.read_text())
    except Exception:
        pass
    return {"offset": 0, "bot_username": None}


def _save_state(st: dict) -> None:
    try:
        BRIDGE_DIR.mkdir(parents=True, exist_ok=True)
        BOT_STATE.write_text(json.dumps(st))
    except Exception as e:
        print(f"[newsbot] state save: {e}", file=sys.stderr)


def _get_bot_username() -> str:
    try:
        me = _tg("getMe")
        return (me.get("result") or {}).get("username", "")
    except Exception:
        return ""


# --- Tool-Runner ---

def _py(script: str, *args: str, timeout: int = 120) -> str:
    try:
        proc = subprocess.run(
            [sys.executable, str(REPO_ROOT / "tools" / script), *args],
            capture_output=True, text=True, timeout=timeout, cwd=str(REPO_ROOT),
        )
        return proc.stdout or proc.stderr
    except Exception as e:
        return f"error: {e}"


# --- Command-Handler ---

def cmd_news() -> str:
    q = BRIDGE_DIR / "signal_queue.jsonl"
    if not q.exists():
        return "Keine News im Queue."
    lines = ["*📰 Letzte News:*"]
    news_items = []
    for l in q.read_text().splitlines()[-200:]:
        try:
            s = json.loads(l)
            if s.get("source") == "crypto-news":
                news_items.append(s)
        except Exception:
            continue
    for s in news_items[-10:]:
        head = s.get("headline") or s.get("name") or "(no title)"
        sev = s.get("severity", "normal")
        icon = "🔥" if sev == "high" else "📰"
        if s.get("public_figure"):
            icon = "👤 🔥"
        lines.append(f"{icon} {head[:120]}")
    return "\n".join(lines) if len(lines) > 1 else "Keine News gefunden."


def cmd_whales() -> str:
    q = BRIDGE_DIR / "signal_queue.jsonl"
    if not q.exists():
        return "Keine Whale-Daten."
    whales = []
    for l in q.read_text().splitlines()[-500:]:
        try:
            s = json.loads(l)
            if s.get("source") == "crypto-onchain":
                whales.append(s)
        except Exception:
            continue
    if not whales:
        return "Keine Whale-Transfers im Queue."
    lines = ["*🐋 Letzte Whales:*"]
    for w in whales[-10:]:
        sym = w.get("symbol", "?")
        usd = w.get("amount_usd", 0)
        sig = w.get("signal", "")
        chain = w.get("chain", "?")
        lines.append(f"• `{sym}` ${usd:,.0f} auf {chain} — {sig}")
    return "\n".join(lines)


def cmd_sentiment() -> str:
    out = _py("sentiment.py", timeout=60)
    try:
        d = json.loads(out)
        return (
            f"*📊 Marktstimmung:*\n"
            f"Fear & Greed: *{d.get('fear_greed_index')}* ({d.get('fear_greed_label')})\n"
            f"Funding 8h: {d.get('funding_rate_8h', '-')}\n"
            f"L/S Ratio: {d.get('long_short_ratio', '-')}\n"
            f"Verdict: *{d.get('verdict')}*\n\n"
            f"_Fear = Kaufgelegenheit, Greed = Vorsicht_"
        )
    except Exception:
        return "Keine Sentiment-Daten verfügbar."


def cmd_dex() -> str:
    out = _py("dex_monitor.py", "--min-change", "15", timeout=90)
    try:
        d = json.loads(out.strip().splitlines()[0] if out.strip() else "[]")
    except Exception:
        d = []
    if not d:
        return "Keine neuen Dex-Mover gefunden."
    lines = ["*🚀 Neue Memecoin-Mover (Dexscreener):*"]
    for row in d[:8]:
        sym = row.get("symbol", "?")
        chg = row.get("price_change_24h", 0)
        liq = row.get("liquidity_usd", 0) / 1000
        vol = row.get("volume_24h_usd", 0) / 1000
        chain = row.get("chain", "?")
        age = row.get("age_hours", 0)
        lines.append(f"• `{sym}` auf {chain} {chg:+.1f}% | Liq ${liq:.0f}k Vol ${vol:.0f}k | {age:.0f}h alt")
    return "\n".join(lines)


def cmd_scan_async(chat_id: str) -> str:
    """Scan ist zu lang für sofortige Antwort — als Background triggern."""
    send(chat_id, "🔍 Scanner startet… das dauert ca. 1-2 Minuten. Ich melde mich mit den Top-Setups.")
    out = _py("scanner.py", "--top-n", "20", timeout=180)
    try:
        hits = json.loads(out.strip().split("\n")[0]) if out.strip() else []
    except Exception:
        hits = []
    if not hits:
        return "✅ Scan fertig — keine klaren Setups aktuell."
    lines = ["*🎯 Top-Setups:*"]
    for h in hits[:8]:
        lines.append(
            f"• `{h.get('symbol')}` {h.get('setup')} @ ${h.get('price')} "
            f"(Q={h.get('quality')}, volZ={h.get('volume_zscore', '-')})"
        )
    lines.append("\n_Vollständige Pipeline läuft im nächsten 5-Min-Cron._")
    return "\n".join(lines)


def cmd_signals() -> str:
    q = BRIDGE_DIR / "signal_queue.jsonl"
    if not q.exists():
        return "signal_queue leer"
    tail = q.read_text().splitlines()[-20:]
    lines = ["*📝 Letzte 10 Signale:*"]
    for l in tail[-10:]:
        try:
            s = json.loads(l)
            lines.append(
                f"• `{s.get('source', '?')}` {s.get('symbol', '')} "
                f"{s.get('setup') or s.get('verdict') or s.get('signal') or ''}"
            )
        except Exception:
            continue
    return "\n".join(lines)


def cmd_status() -> str:
    try:
        from _common import load_config
        cfg = load_config()
        b = cfg["broker"]
        pause = BRIDGE_DIR / "auto_trader.paused"
        paused = "⏸ pausiert" if pause.exists() else "▶️ aktiv"
        return (
            f"*🎯 Trading-Bot Status:*\n"
            f"Exchange: `{b['exchange']}` ({b.get('market', 'spot')})\n"
            f"Modus: `{b['mode']}`\n"
            f"Auto-Trader: {paused}\n"
            f"Risk/Trade: {cfg['budget']['risk_per_trade_pct']}% von ${cfg['budget']['account_equity_usd']}"
        )
    except Exception as e:
        return f"Status-Fehler: {e}"


# --- Gemini-Research ---

def _recent_context(limit: int = 30) -> str:
    """Baut Live-Markt-Kontext aus signal_queue für Gemini."""
    q = BRIDGE_DIR / "signal_queue.jsonl"
    if not q.exists():
        return ""
    rows = []
    for l in q.read_text().splitlines()[-200:]:
        try:
            rows.append(json.loads(l))
        except Exception:
            continue
    # Kompakt zusammenfassen
    news = [r for r in rows if r.get("source") == "crypto-news"][-10:]
    whales = [r for r in rows if r.get("source") == "crypto-onchain"][-5:]
    scanner_hits = [r for r in rows if r.get("source") == "crypto-scanner"][-5:]

    parts = []
    if news:
        parts.append("Jüngste News:")
        for n in news:
            parts.append(f"- [{n.get('severity', '?')}] {n.get('headline', n.get('name', ''))[:140]}")
    if whales:
        parts.append("\nJüngste Whales:")
        for w in whales:
            parts.append(f"- {w.get('symbol', '?')} ${w.get('amount_usd', 0):,.0f} {w.get('signal', '')}")
    if scanner_hits:
        parts.append("\nAktuelle Scanner-Setups:")
        for s in scanner_hits:
            parts.append(f"- {s.get('symbol', '')} {s.get('setup', '')} Q={s.get('quality', '-')}")
    return "\n".join(parts)


def ask_gemini(question: str, context: str = "") -> tuple[str, str | None]:
    """Fragt Gemini und gibt (antwort, action) zurück. action = 'scan', 'open BTC', etc."""
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        return (
            "Gemini-API-Key fehlt — ich kann aktuell keine freien Fragen beantworten. "
            "Nutze /help für die Befehle.",
            None,
        )
    import json as _json
    from urllib.request import Request as _R, urlopen as _U

    full_prompt = f"{GEMINI_SYSTEM}\n\nMARKT-KONTEXT:\n{context or '(keine aktuellen Daten)'}\n\nFRAGE: {question}"
    body = {
        "contents": [{"parts": [{"text": full_prompt}]}],
        "generationConfig": {"temperature": 0.4, "maxOutputTokens": 800},
    }
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={key}"
    try:
        req = _R(url, data=_json.dumps(body).encode(), headers={"Content-Type": "application/json"})
        with _U(req, timeout=30) as r:
            data = _json.loads(r.read().decode())
    except Exception as e:
        return (f"Gemini-Fehler: {e}", None)

    try:
        text = data["candidates"][0]["content"]["parts"][0]["text"].strip()
    except Exception:
        return ("Gemini antwortete leer.", None)

    # Action-Prefix extrahieren
    action = None
    m = re.match(r"ACTION:(\S+(?:\s+\S+)?)\s*\n?(.*)", text, re.DOTALL)
    if m:
        action = m.group(1).strip()
        text = m.group(2).strip() or f"Soll ich *{action}* ausführen?"
    return (text, action)


def cmd_research(topic: str) -> tuple[str, str | None]:
    if not topic.strip():
        return ("Nutzung: `/research <topic>` — z.B. `/research Solana Pump`", None)
    ctx = _recent_context()
    answer, action = ask_gemini(f"Research-Anfrage: {topic}", ctx)
    return (answer, action)


# --- Welcome-Handler ---

def handle_new_members(msg: dict) -> None:
    chat_id = str((msg.get("chat") or {}).get("id", ""))
    new_members = msg.get("new_chat_members") or []
    bot_un = _load_state().get("bot_username", "")
    for m in new_members:
        # Skip wenn Bot selbst der neue "Member" ist
        if m.get("is_bot") and m.get("username") == bot_un:
            continue
        name = m.get("first_name") or m.get("username") or "neuer Trader"
        welcome = WELCOME_DE.format(name=name)
        send(chat_id, welcome)


# --- Message-Handler ---

def handle_message(update: dict) -> None:
    msg = update.get("message") or update.get("edited_message") or {}
    if not msg:
        return
    chat_id = str((msg.get("chat") or {}).get("id", ""))
    if not chat_id:
        return

    # 1) Welcome für neue Mitglieder
    if msg.get("new_chat_members"):
        handle_new_members(msg)
        return

    text = (msg.get("text") or "").strip()
    if not text:
        return

    msg_id = msg.get("message_id")
    user = msg.get("from") or {}
    user_name = user.get("first_name", "")

    # Nur in erlaubten Chats (News-Gruppe + optional andere via env)
    news_chat = _news_chat_id()
    allowed_chats = set()
    if news_chat:
        allowed_chats.add(news_chat)
    extra = os.environ.get("NEWS_BOT_EXTRA_CHATS", "")
    for c in extra.split(","):
        if c.strip():
            allowed_chats.add(c.strip())
    if allowed_chats and chat_id not in allowed_chats:
        return  # Fremde Chats ignorieren

    bot_un = _load_state().get("bot_username", "")

    # Commands
    if text.startswith("/"):
        parts = text.split()
        cmd = parts[0].split("@")[0].lower()
        args = " ".join(parts[1:])
        try:
            if cmd == "/help" or cmd == "/start":
                send(chat_id, HELP_DE, reply_to=msg_id)
            elif cmd == "/news":
                send(chat_id, cmd_news(), reply_to=msg_id)
            elif cmd == "/whales":
                send(chat_id, cmd_whales(), reply_to=msg_id)
            elif cmd == "/sentiment":
                send(chat_id, cmd_sentiment(), reply_to=msg_id)
            elif cmd == "/dex":
                send(chat_id, cmd_dex(), reply_to=msg_id)
            elif cmd == "/signals":
                send(chat_id, cmd_signals(), reply_to=msg_id)
            elif cmd == "/scan":
                reply = cmd_scan_async(chat_id)
                send(chat_id, reply, reply_to=msg_id)
            elif cmd == "/status":
                send(chat_id, cmd_status(), reply_to=msg_id)
            elif cmd == "/research":
                answer, action = cmd_research(args)
                send(chat_id, answer, reply_to=msg_id)
                if action:
                    send(chat_id, f"Soll ich *{action}* ausführen? Antworte mit *ja* oder *nein*.")
            else:
                send(chat_id, "Unbekannter Befehl. `/help` zeigt alle Optionen.", reply_to=msg_id)
        except Exception as e:
            send(chat_id, f"Fehler: {e}", reply_to=msg_id)
        return

    # Nicht-Command: nur antworten wenn @mention oder Reply auf Bot
    reply_to_msg = msg.get("reply_to_message") or {}
    reply_to_bot = (reply_to_msg.get("from") or {}).get("username") == bot_un
    mentioned = bool(bot_un and (f"@{bot_un}".lower() in text.lower()))

    # In Private-Chats (positive IDs) immer antworten
    is_private = not chat_id.startswith("-")

    if not (reply_to_bot or mentioned or is_private):
        return

    # Mention entfernen aus Frage
    clean_question = re.sub(rf"@{bot_un}", "", text, flags=re.I).strip()
    if not clean_question:
        send(chat_id, "Was möchtest du wissen? Nutze /help für die Befehle oder stell direkt eine Frage.", reply_to=msg_id)
        return

    # Gemini antworten lassen
    ctx = _recent_context()
    answer, action = ask_gemini(clean_question, ctx)
    full_reply = f"{answer}\n\n_Keine Finanz-Beratung, DYOR._"
    send(chat_id, full_reply, reply_to=msg_id)
    if action:
        send(chat_id, f"➡️ Vorgeschlagene Aktion: *{action}*\nSoll ich das ausführen? Antworte mit *ja* oder *nein*.")


# --- Main-Loop ---

def main() -> int:
    try:
        _tg_token()
    except Exception as e:
        print(f"[newsbot] {e}", file=sys.stderr)
        return 2

    state = _load_state()
    if not state.get("bot_username"):
        state["bot_username"] = _get_bot_username()
        _save_state(state)
    print(f"[newsbot] bot @{state.get('bot_username', '?')} starting long-poll", file=sys.stderr)

    offset = state.get("offset", 0)
    while True:
        try:
            resp = _tg(
                "getUpdates",
                offset=offset,
                timeout=25,
                allowed_updates=json.dumps(["message", "edited_message"]),
            )
            for u in resp.get("result", []):
                offset = u["update_id"] + 1
                try:
                    handle_message(u)
                except Exception as e:
                    print(f"[newsbot] handle error: {e}", file=sys.stderr)
            state["offset"] = offset
            _save_state(state)
        except KeyboardInterrupt:
            print("[newsbot] bye", file=sys.stderr)
            return 0
        except Exception as e:
            print(f"[newsbot] poll error: {e}", file=sys.stderr)
            time.sleep(5)


if __name__ == "__main__":
    sys.exit(main())
