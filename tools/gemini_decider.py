#!/usr/bin/env python3
"""Gemini LLM-Decider — zweite Meinung für jeden Trade-Vorschlag.

Nimmt einen kompletten Trade-Proposal (Scanner + Exit + Timemachine +
Risk + Budget + aktuelle News + Sentiment + On-chain) und schickt ihn an
Google's Gemini. Gemini gibt ein strukturiertes JSON zurück:

  {
    "verdict": "GO" | "SKIP" | "WAIT",
    "confidence": 0.0 - 1.0,
    "reasoning": "kurz auf Deutsch",
    "risk_flags": ["..."],  // optionale Warnungen
  }

Benutzung aus pipeline_once.py:
    from tools.gemini_decider import review_trade
    verdict = review_trade(proposal_dict)
    if verdict["verdict"] != "GO":
        return skip

API-Key via env GEMINI_API_KEY. Ohne Key: tool skipt sich selbst
(decision: no-op → bisherige Rules-Policy entscheidet).

Model: gemini-1.5-flash (billig, schnell, reicht für Entscheidungen).
Rate-Limit free tier: 15 RPM, 1M TPM, 1500 RPD. Reicht für 15-Min-Cron.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from urllib.request import Request, urlopen


GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"
DEFAULT_MODEL = "gemini-1.5-flash"


SYSTEM_PROMPT = """Du bist ein kritischer Trading-Risk-Analyst für Krypto-Spot-Trading.
Du bewertest einen vorgeschlagenen Trade basierend auf:
1. Technischen Setup (Scanner-Signal, Stop/Target, Monte-Carlo-Odds)
2. Markt-Kontext (News, Sentiment, Whale-Bewegungen)
3. Risiko-Metriken (Position-Size, Max-Loss)

Antworte NUR mit einem JSON-Objekt (ohne Markdown-Fences):
{
  "verdict": "GO" oder "SKIP" oder "WAIT",
  "confidence": 0.0 bis 1.0,
  "reasoning": "ein Satz auf Deutsch warum",
  "risk_flags": ["warnung1", "warnung2"]
}

GO = Trade jetzt eröffnen, Edge klar positiv und Kontext passt
SKIP = Trade NICHT nehmen, erkennbares Problem
WAIT = Setup okay aber warte auf Bestätigung (Volumen/News)

Sei konservativ. Day-Trading verliert statistisch Geld. Standard = SKIP wenn:
- E[R] < 0.3 (zu wenig Edge für Kommissionen+Slippage)
- News-Sentiment stark negativ bei Long-Setup
- Whale-Verkaufsdruck direkt vor Entry
- Sentiment "extended" wenn man in überhitzten Markt kauft
"""


def _tg_notify(text: str) -> None:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat = os.environ.get("TELEGRAM_CHAT_ID")
    if not (token and chat):
        return
    from urllib.parse import urlencode
    try:
        data = urlencode({"chat_id": chat, "text": text[:4000],
                          "parse_mode": "Markdown"}).encode()
        urlopen(Request(f"https://api.telegram.org/bot{token}/sendMessage",
                        data=data), timeout=15).read()
    except Exception:
        pass


def review_trade(proposal: dict, model: str = DEFAULT_MODEL) -> dict:
    """Fragt Gemini nach einer Meinung zum Trade.
    Returns dict mit 'verdict' (GO|SKIP|WAIT), 'confidence', 'reasoning', 'risk_flags'.
    """
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        return {"verdict": "NO_OP", "confidence": 0, "reasoning": "GEMINI_API_KEY nicht gesetzt",
                "risk_flags": []}

    # Serialisiere relevante Daten kompakt
    plan = proposal.get("plan", {})
    tm = proposal.get("timemachine") or proposal.get("tm", {})
    size = proposal.get("size") or proposal.get("budget", {})
    news = proposal.get("news", [])
    onchain = proposal.get("onchain", [])
    sentiment = proposal.get("sentiment", {})

    user_prompt = f"""TRADE-VORSCHLAG:
Symbol: {plan.get('symbol')} ({plan.get('side', 'long')})
Setup: {proposal.get('scanner_hit', {}).get('setup', '?')}
Entry: ${plan.get('entry')}
Stop-Loss: ${plan.get('stop')} ({(plan.get('stop', 0)/plan.get('entry', 1) - 1)*100:+.2f}%)
Take-Profit: ${plan.get('target')} ({(plan.get('target', 0)/plan.get('entry', 1) - 1)*100:+.2f}%)
R:R: {plan.get('reward_risk')}
Position: ${size.get('notional_usd')} ({size.get('base_qty')} units, max Risk ${size.get('dollar_risk_at_stop')})

MONTE-CARLO (2000 Simulationen):
P(Target zuerst): {tm.get('p_target_first', 0)*100:.1f}%
P(Stop zuerst): {tm.get('p_stop_first', 0)*100:.1f}%
Expected R: {tm.get('expected_r')}
Historische Trefferquote: {(tm.get('historical_win_rate') or 0)*100:.0f}%

MARKT-KONTEXT:
Fear & Greed: {sentiment.get('fear_greed_index')} ({sentiment.get('fear_greed_label')})
Funding 8h: {sentiment.get('funding_rate_8h')}
Verdict: {sentiment.get('verdict')}

RELEVANTE NEWS:
{chr(10).join('- ' + str(n.get('headline', ''))[:150] + f" ({n.get('sentiment')})" for n in news[:5]) or 'keine high-impact news'}

WHALE-FLOWS:
{chr(10).join(f"- {w.get('chain')} {w.get('symbol')}: ${w.get('amount_usd', 0):,.0f} — {w.get('signal')}" for w in onchain[:3]) or 'keine großen Transfers'}

Bewerte diesen Trade. Antworte nur mit JSON."""

    body = {
        "systemInstruction": {"parts": [{"text": SYSTEM_PROMPT}]},
        "contents": [{"role": "user", "parts": [{"text": user_prompt}]}],
        "generationConfig": {
            "temperature": 0.3,
            "maxOutputTokens": 500,
            "responseMimeType": "application/json",
        },
    }

    url = GEMINI_URL.format(model=model, key=key)
    try:
        req = Request(url, data=json.dumps(body).encode(),
                      headers={"Content-Type": "application/json"})
        with urlopen(req, timeout=30) as r:
            resp = json.loads(r.read().decode())
    except Exception as e:
        print(f"[gemini] API error: {e}", file=sys.stderr)
        return {"verdict": "ERROR", "confidence": 0, "reasoning": str(e), "risk_flags": []}

    # Parse response
    try:
        text = resp["candidates"][0]["content"]["parts"][0]["text"]
        verdict = json.loads(text)
        # Validate shape
        if "verdict" not in verdict:
            raise ValueError("missing verdict field")
        verdict.setdefault("confidence", 0.5)
        verdict.setdefault("reasoning", "")
        verdict.setdefault("risk_flags", [])
        return verdict
    except Exception as e:
        print(f"[gemini] parse error: {e}; response={resp}", file=sys.stderr)
        return {"verdict": "ERROR", "confidence": 0, "reasoning": f"parse: {e}", "risk_flags": []}


def format_gemini_message(symbol: str, verdict: dict) -> str:
    v = verdict.get("verdict", "?")
    conf = verdict.get("confidence", 0)
    icon = {"GO": "✅", "SKIP": "🚫", "WAIT": "⏸", "NO_OP": "⚪", "ERROR": "⚠️"}.get(v, "❓")
    flags = verdict.get("risk_flags") or []
    flag_block = ("\n⚠️ *Warnungen:*\n" + "\n".join(f"• {f}" for f in flags)) if flags else ""
    return (
        f"🤖 *Gemini-Review: `{symbol}`*\n"
        f"{icon} Verdict: *{v}* (Konfidenz: {conf:.0%})\n"
        f"_{verdict.get('reasoning', '')}_"
        f"{flag_block}"
    )


# --- CLI (für manuellen Test) ---

def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--proposal", required=True, help="Pfad zu JSON mit Trade-Proposal")
    p.add_argument("--notify", action="store_true", help="Ergebnis an Telegram pushen")
    args = p.parse_args()

    proposal = json.loads(Path(args.proposal).read_text())
    result = review_trade(proposal)
    print(json.dumps(result, indent=2, ensure_ascii=False))

    if args.notify:
        symbol = proposal.get("plan", {}).get("symbol", "?")
        _tg_notify(format_gemini_message(symbol, result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
