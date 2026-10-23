#!/usr/bin/env python3
"""News-Headlines via Gemini auf Deutsch übersetzen (optional).

Wird im push_signals.py aufgerufen BEVOR eine News an Telegram geht.
Ohne GEMINI_API_KEY → kein Übersetzen (News bleibt EN).
Cache: data/snapshots/news_translations.json damit nicht doppelt bezahlt wird.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from urllib.request import Request, urlopen

REPO = Path(__file__).resolve().parent.parent
CACHE_FILE = REPO / "data" / "snapshots" / "news_translations.json"
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={key}"


def _load_cache() -> dict:
    if CACHE_FILE.exists():
        try:
            return json.loads(CACHE_FILE.read_text())
        except Exception:
            pass
    return {}


def _save_cache(cache: dict) -> None:
    CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    # Cap at 500 entries to avoid unbounded growth
    if len(cache) > 500:
        items = list(cache.items())[-500:]
        cache = dict(items)
    CACHE_FILE.write_text(json.dumps(cache, ensure_ascii=False, indent=2))


def translate(text: str) -> str | None:
    """Übersetzt EN→DE via Gemini. Returns None wenn kein Key / Fehler."""
    text = (text or "").strip()
    if not text:
        return None
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        return None

    cache = _load_cache()
    cache_key = text[:200]
    if cache_key in cache:
        return cache[cache_key]

    body = {
        "contents": [{"role": "user", "parts": [{
            "text": f"Übersetze diese Krypto-Nachrichten-Headline auf Deutsch. "
                    f"NUR die Übersetzung, keine Erklärung, kein Markdown.\n\n{text}"
        }]}],
        "generationConfig": {"temperature": 0.1, "maxOutputTokens": 200},
    }
    try:
        req = Request(GEMINI_URL.format(key=key),
                      data=json.dumps(body).encode(),
                      headers={"Content-Type": "application/json"})
        with urlopen(req, timeout=15) as r:
            resp = json.loads(r.read().decode())
        translation = resp["candidates"][0]["content"]["parts"][0]["text"].strip()
        # Clean CDATA artifacts
        translation = translation.replace("<![CDATA[", "").replace("]]>", "").strip()
        cache[cache_key] = translation
        _save_cache(cache)
        return translation
    except Exception as e:
        import sys
        print(f"[translate] {e}", file=sys.stderr)
        return None
