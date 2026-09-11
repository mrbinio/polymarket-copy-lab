#!/usr/bin/env python3
"""After Claude: Grok searches X for news-spike vs series on the shortlist.

Does not enable copy. Does not place trades. Numbers stay the veto: if conc>0.45,
candles, or CopyGrade Avoid, Grok cannot clear them. Grok may only add news_spike
to x_skip so the next hunt_next.py cycle does not re-queue that nick.
"""

from __future__ import annotations

import json
import os
import re
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
HUNT = ROOT / "ops" / "data" / "hunt.json"
OUT = ROOT / "ops" / "data" / "solutions.json"
MODELS = (
    os.environ.get("XAI_MODEL") or "grok-4.6",
    "grok-4",
    "grok-3",
)
BANNED_PHRASES = ("włącz copy", "turn on copy", "turn on all", "dokup", "deposit more", "kup ")

SYSTEM = """You are the Copy Lab X filter. PolyCop Telegram is the only executor. Copy stays OFF.
You search X (Twitter) for the shortlist wallets' top markets. Decide if recent PnL is a news spike
(one match, one vote, one post) or a repeatable series. Never tell anyone to enable copy, deposit,
or Turn On All Copy. Never override concentration>0.45, 5-min candles, or CopyGrade Avoid — those
already fail. If X shows one event, verdict is news_spike. If X is thin, verdict is unclear.
Search X at most twice. Last 14 days only. Return ONLY JSON."""


def load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def parse_model_json(text: str) -> dict[str, Any] | None:
    raw = (text or "").strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.I | re.S)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", raw, re.S)
        if not m:
            return None
        try:
            data = json.loads(m.group(0))
        except json.JSONDecodeError:
            return None
    return data if isinstance(data, dict) else None


def row_by_name(hunt: dict[str, Any]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for row in hunt.get("candidates") or []:
        name = str(row.get("username") or "").strip()
        if name:
            out[name.lower()] = row
    pick = hunt.get("pick") or {}
    pname = str(pick.get("username") or "").strip()
    if pname and pname.lower() not in out:
        out[pname.lower()] = pick
    return out


def shortlist(sol: dict[str, Any], hunt: dict[str, Any]) -> list[dict[str, Any]]:
    names: list[str] = []
    closest = ((sol.get("closest") or {}).get("name") or "").strip()
    if closest and closest != "—":
        names.append(closest)
    scored = []
    for row in hunt.get("candidates") or []:
        name = str(row.get("username") or "").strip()
        if not name:
            continue
        try:
            w60 = float(row.get("w60") or 0)
            w90 = float(row.get("w90") or 0)
            candle = float(row.get("candle") or 0)
        except (TypeError, ValueError):
            continue
        if w60 <= 0 or w90 <= 0 or candle > 0.25:
            continue
        cat = str(row.get("cat") or "")
        pri = 0 if cat in ("SPORTS", "POLITICS") else 1
        scored.append((pri, -w90, name, row))
    scored.sort()
    for _, _, name, _ in scored:
        if name not in names:
            names.append(name)
        if len(names) >= 5:
            break
    by = row_by_name(hunt)
    items = []
    for name in names[:5]:
        row = by.get(name.lower()) or {"username": name}
        items.append({
            "username": row.get("username") or name,
            "cat": row.get("cat"),
            "top_market": row.get("top_market"),
            "w60": row.get("w60"),
            "w90": row.get("w90"),
            "conc": row.get("conc"),
            "copies": row.get("copies"),
            "candle": row.get("candle"),
            "days": row.get("history_days"),
        })
    return items


def output_text(blob: dict[str, Any]) -> str:
    if isinstance(blob.get("output_text"), str) and blob["output_text"].strip():
        return blob["output_text"]
    bits: list[str] = []
    for item in blob.get("output") or []:
        if not isinstance(item, dict):
            continue
        content = item.get("content")
        if isinstance(content, str):
            bits.append(content)
            continue
        for part in content or []:
            if isinstance(part, dict):
                if part.get("type") in ("output_text", "text"):
                    bits.append(part.get("text") or "")
            elif isinstance(part, str):
                bits.append(part)
    return "\n".join(bits)


def did_x_search(blob: dict[str, Any]) -> bool:
    for item in blob.get("output") or []:
        if not isinstance(item, dict):
            continue
        kind = str(item.get("type") or "")
        if "x_search" in kind or kind.endswith("search_call"):
            return True
    return False


def call_grok(payload: dict[str, Any]) -> tuple[dict[str, Any] | None, str | None, str | None]:
    key = (os.environ.get("XAI_API_KEY") or "").strip()
    if not key:
        return None, None, None
    since = (datetime.now(timezone.utc) - timedelta(days=14)).strftime("%Y-%m-%d")
    user = (
        "Search X for these Polymarket names/markets. Reply JSON: "
        "items[{name,verdict,why_pl,why_en}] where verdict is news_spike, series, or unclear; "
        "summary_pl; summary_en. enable must be false. At most two X searches.\n\n"
        + json.dumps(payload, ensure_ascii=False)[:8000]
    )
    last_err = None
    for model in MODELS:
        body = json.dumps({
            "model": model,
            "store": False,
            "max_output_tokens": 800,
            "tools": [{"type": "x_search", "from_date": since}],
            "input": [
                {"role": "system", "content": SYSTEM},
                {"role": "user", "content": user},
            ],
        }).encode()
        req = urllib.request.Request(
            "https://api.x.ai/v1/responses",
            data=body,
            headers={
                "Authorization": "Bearer " + key,
                "content-type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                blob = json.loads(resp.read().decode())
        except urllib.error.HTTPError as exc:
            last_err = "http %s %s" % (exc.code, model)
            err_body = exc.read().decode()[:200]
            if exc.code in (400, 404, 422):
                continue
            return None, last_err + " " + err_body, None
        except Exception as exc:  # noqa: BLE001
            return None, str(exc)[:160], None
        if not did_x_search(blob):
            last_err = "no x_search on " + model
            continue
        parsed = parse_model_json(output_text(blob if isinstance(blob, dict) else {}))
        if parsed:
            return parsed, None, model
        last_err = "bad json from " + model
    return None, last_err, None


def numeric_ok(row: dict[str, Any] | None) -> bool:
    if not row:
        return False
    try:
        if float(row.get("conc") or 1) > 0.45:
            return False
        if float(row.get("candle") or 0) > 0.25:
            return False
        if int(row.get("copies") or 0) < 6:
            return False
        if float(row.get("history_days") or row.get("days") or 0) < 90:
            return False
        cg = str((row.get("copygrade") or {}).get("label")
                 or (row.get("copygrade") or {}).get("status") or "")
        if "Avoid" in cg:
            return False
    except (TypeError, ValueError):
        return False
    return True


def apply_filter(
    sol: dict[str, Any],
    hunt: dict[str, Any],
    parsed: dict[str, Any] | None,
    err: str | None,
    model: str | None,
) -> dict[str, Any]:
    out = dict(sol)
    out["enable"] = False
    out["verb_pl"] = "NIE WŁĄCZAJ"
    out["verb_en"] = "DO NOT ENABLE"
    by = row_by_name(hunt)
    if parsed is None:
        out["x_source"] = "off" if not err else "error"
        out["x_model"] = None
        if err:
            out["x_error"] = err[:160]
            out["x_pl"] = "Filtr X: błąd API. Zostają sity. Copy off."
            out["x_en"] = "X filter: API error. Gates stand. Copy off."
        else:
            out["x_pl"] = "Filtr X czeka na secret XAI_API_KEY. Claude/liczby bez zmian. Copy off."
            out["x_en"] = "X filter waiting for XAI_API_KEY. Claude/numbers unchanged. Copy off."
        return out

    items_out = []
    skip: list[str] = []
    for raw in parsed.get("items") or []:
        if not isinstance(raw, dict):
            continue
        name = str(raw.get("name") or "").strip()[:40]
        if not name:
            continue
        verdict = str(raw.get("verdict") or "unclear").strip().lower()
        if verdict not in ("news_spike", "series", "unclear"):
            verdict = "unclear"
        row = by.get(name.lower())
        # Numbers win: Grok cannot call a one-event / candle book a series.
        if verdict == "series" and row and not numeric_ok(row) and (
            float(row.get("conc") or 1) > 0.45 or float(row.get("candle") or 0) > 0.25
        ):
            verdict = "news_spike"
        why_pl = str(raw.get("why_pl") or "")[:160]
        why_en = str(raw.get("why_en") or why_pl)[:160]
        items_out.append({
            "name": name,
            "verdict": verdict,
            "why_pl": why_pl,
            "why_en": why_en,
        })
        if verdict == "news_spike":
            skip.append(name)

    closest_name = str((out.get("closest") or {}).get("name") or "")
    spiked = {n.lower() for n in skip}
    if closest_name.lower() in spiked:
        nxt = None
        for row in hunt.get("candidates") or []:
            n = str(row.get("username") or "").strip()
            if n and n.lower() not in spiked and float(row.get("candle") or 0) <= 0.25:
                nxt = row
                break
        if nxt:
            n = str(nxt.get("username"))
            lane = str(nxt.get("cat") or "")
            missing = str((out.get("closest") or {}).get("missing") or "sity")
            out["closest"] = {"name": n, "lane": lane, "missing": missing}
            out["path_pl"] = (
                "Filtr X: %s = news / jeden event, nie seria. Najbliżej dalej: %s (%s). "
                "Brakuje: %s. W Telegramie nic nie klikasz."
                % (closest_name, n, lane, missing)
            )[:420]
            out["path_en"] = (
                "X filter: %s = news / one event, not a series. Next closest: %s (%s). "
                "Missing: %s. Do nothing in Telegram."
                % (closest_name, n, lane, missing)
            )[:420]
        else:
            out["path_pl"] = (
                "Filtr X odciął najbliższy nick (%s) jako news. Hunt idzie dalej. Nic nie włączasz."
                % closest_name
            )[:420]
            out["path_en"] = (
                "X filter dropped the closest nick (%s) as news. Hunt continues. Do not enable."
                % closest_name
            )[:420]
        dead = list(out.get("dead_pl") or [])
        line = "%s · X · news_spike" % closest_name
        if line not in dead:
            out["dead_pl"] = ([line] + dead)[:4]
            out["dead_en"] = ([line] + list(out.get("dead_en") or dead))[:4]

    summary_pl = str(parsed.get("summary_pl") or "").strip()[:280]
    summary_en = str(parsed.get("summary_en") or "").strip()[:280]
    if skip:
        default_pl = "Filtr X: %s = news / jeden event, nie seria. Hunt idzie dalej. Copy off." % ", ".join(skip)
        default_en = "X filter: %s = news / one event, not a series. Hunt continues. Copy off." % ", ".join(skip)
    else:
        default_pl = "Filtr X: na krótkiej liście brak news-spike. Sity bez zmian. Copy off."
        default_en = "X filter: no news-spike on the shortlist. Gates unchanged. Copy off."
    out["x_pl"] = summary_pl or default_pl
    out["x_en"] = summary_en or default_en
    low = (out["x_pl"] + out["x_en"] + out.get("path_pl", "")).lower()
    if any(b in low for b in BANNED_PHRASES):
        out["x_pl"] = default_pl
        out["x_en"] = default_en
        out["path_pl"] = sol.get("path_pl") or out.get("path_pl")
        out["path_en"] = sol.get("path_en") or out.get("path_en")
        out["closest"] = sol.get("closest") or out.get("closest")
    out["x_source"] = "grok"
    out["x_model"] = model
    out["x_skip"] = skip[:8]
    out["x_items"] = items_out[:5]
    return out


def main() -> int:
    hunt = load_json(HUNT)
    sol = load_json(OUT)
    if not sol:
        print("no solutions.json — run Claude first", file=sys.stderr)
        return 0
    names = shortlist(sol, hunt)
    parsed, err, model = call_grok({"shortlist": names})
    out = apply_filter(sol, hunt, parsed, err, model)
    out["enable"] = False
    out["x_updated_at"] = datetime.now(timezone.utc).isoformat()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, indent=2, ensure_ascii=False) + "\n")
    print(
        "wrote", OUT, "x_source", out.get("x_source"),
        "skip", out.get("x_skip"), file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
