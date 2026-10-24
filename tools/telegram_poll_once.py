#!/usr/bin/env python3
"""telegram_poll_once: One-shot getUpdates für Cron-basierte Bot-Steuerung.

Zweck: GitHub Actions kann kein echtes Long-Polling. Dieses Script holt
EINMAL alle neuen Updates, verarbeitet sie, speichert den Offset und
beendet sich. Via 5-Min-Cron läuft es wie ein (langsamer) Command-Bot.

Handhabt BEIDE Bots in einem Lauf:
  - TELEGRAM_BOT_TOKEN  (Trading-Bot, Owner-only Commands)
  - TELEGRAM_NEWS_BOT_TOKEN (News-Bot, Group Q&A — nur falls Gemini-Key da)

Offset-State in data/snapshots/tg_offsets.json (committen vom Workflow).
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).resolve().parent))

REPO_ROOT = Path(__file__).resolve().parent.parent
OFFSETS_FILE = REPO_ROOT / "data" / "snapshots" / "tg_offsets.json"


def _tg(token: str, method: str, **params) -> dict:
    url = f"https://api.telegram.org/bot{token}/{method}"
    data = urlencode(params).encode()
    try:
        with urlopen(Request(url, data=data), timeout=20) as r:
            return json.loads(r.read().decode())
    except Exception as e:
        return {"ok": False, "error": str(e)}


def _load_offsets() -> dict:
    try:
        if OFFSETS_FILE.exists():
            return json.loads(OFFSETS_FILE.read_text())
    except Exception:
        pass
    return {}


def _save_offsets(d: dict) -> None:
    OFFSETS_FILE.parent.mkdir(parents=True, exist_ok=True)
    OFFSETS_FILE.write_text(json.dumps(d, indent=2))


def poll_trading_bot() -> int:
    """Trading-Bot: Owner-only Commands. Ruft telegram_bot.handle() auf."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        return 0
    offsets = _load_offsets()
    key = f"trading_{token[:10]}"
    offset = offsets.get(key, 0)

    # timeout=0 → sofort returnen, keine Hänger
    resp = _tg(token, "getUpdates", offset=offset, timeout=0, limit=50)
    if not resp.get("ok"):
        print(f"[poll trading] {resp.get('error')}", file=sys.stderr)
        return 0

    from telegram_bot import handle  # noqa: E402
    count = 0
    for u in resp.get("result", []):
        offsets[key] = u["update_id"] + 1
        try:
            handle(u)
            count += 1
        except Exception as e:
            print(f"[poll trading] handle error: {e}", file=sys.stderr)
    _save_offsets(offsets)
    if count:
        print(f"[poll trading] processed {count} updates", file=sys.stderr)
    return count


def poll_news_bot() -> int:
    """News-Bot: Group Q&A. Ruft news_bot.handle_message() auf."""
    token = os.environ.get("TELEGRAM_NEWS_BOT_TOKEN")
    if not token:
        return 0
    offsets = _load_offsets()
    key = f"news_{token[:10]}"
    offset = offsets.get(key, 0)

    # Nur allowed update-types holen
    resp = _tg(
        token, "getUpdates",
        offset=offset, timeout=0, limit=50,
        allowed_updates=json.dumps(["message", "edited_message"]),
    )
    if not resp.get("ok"):
        print(f"[poll news] {resp.get('error')}", file=sys.stderr)
        return 0

    # Bot-Username holen für Mention-Detection
    from news_bot import handle_message, _load_state, _save_state, _get_bot_username  # noqa: E402
    state = _load_state()
    if not state.get("bot_username"):
        state["bot_username"] = _get_bot_username()
        _save_state(state)

    count = 0
    for u in resp.get("result", []):
        offsets[key] = u["update_id"] + 1
        try:
            handle_message(u)
            count += 1
        except Exception as e:
            print(f"[poll news] handle error: {e}", file=sys.stderr)
    _save_offsets(offsets)
    if count:
        print(f"[poll news] processed {count} updates", file=sys.stderr)
    return count


def main() -> int:
    total = poll_trading_bot() + poll_news_bot()
    print(f"[tg-poll] done, {total} updates processed", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
