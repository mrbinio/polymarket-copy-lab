#!/usr/bin/env python3
"""One Claude call after a hunt cycle: closest $5-copy path + next search.

Does not enable copy. Does not place trades. Numbers (60/90, conc, candles,
CopyGrade) stay the veto. Output: ops/data/solutions.json for the board and
the next hunt_next.py cycle.
"""

from __future__ import annotations

import json
import os
import re
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
HUNT = ROOT / "ops" / "data" / "hunt.json"
SNAP = ROOT / "ops" / "data" / "snapshot.json"
OUT = ROOT / "ops" / "data" / "solutions.json"
CATS = ["POLITICS", "CULTURE", "TECH", "FINANCE", "SPORTS", "ECONOMICS"]
MODELS = (
    os.environ.get("ANTHROPIC_MODEL") or "claude-sonnet-5",
    "claude-sonnet-4-5",
    "claude-sonnet-4-20250514",
)

SYSTEM = """You are the Copy Lab research desk. PolyCop Telegram is the only executor.
Copy stays OFF until a wallet clears: history >= 90d, lab sim profit in 60d AND 90d,
concentration <= 0.45, >= 6 copyable fills under $15 spend, not 5-min Up/Down candles,
CopyGrade not Avoid. Never recycle Top 8. Never Turn On All Copy.
Cash is ~$39. Hole is -$13.58 (invorser esports). Caps if ever enabled later: $5 copy,
ignore <$20, $15 total, SL $31. Antblack (tennis, 76d) and 86shin (politics, thin) are
Paused — do not tell the user to enable them.
Your job: find the closest honest path to a reasonable $5 copier on THIS tape, and
tell the next hunt cycle where to look. If nothing clears, the path is hunt — not
deposit more cash, not candles, not All Copy.
Return ONLY JSON, no markdown."""


def load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError:
        return {}


def compact_hunt(hunt: dict[str, Any]) -> dict[str, Any]:
    cands = []
    for row in (hunt.get("candidates") or [])[:8]:
        cands.append({
            "username": row.get("username"),
            "cat": row.get("cat"),
            "domain": row.get("domain"),
            "days": row.get("history_days"),
            "w60": row.get("w60"),
            "w90": row.get("w90"),
            "copies": row.get("copies"),
            "conc": row.get("conc"),
            "top_market": row.get("top_market"),
            "candle": row.get("candle"),
            "score": row.get("score"),
            "copygrade": (row.get("copygrade") or {}).get("label")
            or (row.get("copygrade") or {}).get("status"),
        })
    return {
        "updated_at": hunt.get("updated_at"),
        "checked": hunt.get("checked"),
        "pick": (hunt.get("pick") or {}).get("username"),
        "candidates": cands,
        "journal_wait_n": (hunt.get("journal") or {}).get("wait_n"),
        "lanes": hunt.get("lanes") or [],
    }


def numeric_path(hunt: dict[str, Any], snap: dict[str, Any]) -> dict[str, Any]:
    """Always-on path from lab numbers. Claude may refine, never enable."""
    cands = list(hunt.get("candidates") or [])
    watch = list(snap.get("watch_not_enable") or [])

    def both(row: dict) -> bool:
        try:
            return float(row.get("w60") or 0) > 0 and float(row.get("w90") or 0) > 0
        except (TypeError, ValueError):
            return False

    books = [
        r for r in cands
        if both(r)
        and float(r.get("conc") or 1) <= 0.45
        and float(r.get("candle") or 0) <= 0.25
        and int(r.get("copies") or 0) >= 6
    ]
    books.sort(key=lambda r: float(r.get("w90") or 0), reverse=True)
    almost = [
        r for r in cands
        if both(r) and float(r.get("candle") or 0) <= 0.25
        and r not in books
    ]
    almost.sort(key=lambda r: (
        float(r.get("conc") or 1),
        -float(r.get("w90") or 0),
    ))

    closest = books[0] if books else (almost[0] if almost else None)
    missing = "90d + książka + CopyGrade naraz"
    lane = "SPORTS"
    name = "—"
    if closest:
        name = str(closest.get("username") or "—")
        lane = str(closest.get("cat") or "SPORTS")
        days = float(closest.get("history_days") or 0)
        conc = float(closest.get("conc") or 1)
        copies = int(closest.get("copies") or 0)
        gaps = []
        if days < 90:
            gaps.append("track < 90d")
        if conc > 0.45:
            gaps.append("jeden event (conc %.2f)" % conc)
        if copies < 6:
            gaps.append("za mało filli (%d)" % copies)
        cg = str((closest.get("copygrade") or {}).get("label")
                 or (closest.get("copygrade") or {}).get("status") or "")
        if "Avoid" in cg:
            gaps.append("CopyGrade Avoid")
        missing = "; ".join(gaps) if gaps else "sity prawie — nadal WAIT, nie enable"
    elif watch:
        w = watch[0]
        name = str(w.get("name") or "Antblack")
        lane = "SPORTS" if "tennis" in str(w.get("domain") or "").lower() else "POLITICS"
        missing = str(w.get("why_not_yet") or "Paused — nie włączasz")

    both_cats = []
    for r in books:
        cat = str(r.get("cat") or "")
        if cat in CATS and cat not in both_cats:
            both_cats.append(cat)
    next_cats = both_cats[:]
    for c in ("SPORTS", "POLITICS", "FINANCE"):
        if c not in next_cats:
            next_cats.append(c)
    for c in CATS:
        if c not in next_cats:
            next_cats.append(c)
    extra = {}
    if next_cats:
        extra[next_cats[0]] = [250, 300]

    dead = []
    for r in cands[:6]:
        if float(r.get("conc") or 1) > 0.45 or "Avoid" in str((r.get("copygrade") or {})):
            dead.append("%s · %s · jeden event / veto" % (r.get("username"), r.get("cat")))

    now_pl = (
        "Żaden tor nie przeszedł sitów do włączenia. $39 zostaje. "
        "Najbliższa ścieżka to hunt pod książkę $5 / $15, nie dokupowanie i nie All Copy."
    )
    path_pl = (
        "Najbliżej: %s (%s). Brakuje: %s. Patrz w Symulacji. W Telegramie nic nie klikasz."
        % (name, lane, missing)
    )
    hunt_pl = (
        "Następny bieg: głębiej %s (offset 250/300), potem %s. "
        "Pomijamy Top 8, świece 5 min, esport, conc=1.0 jako pick."
        % (next_cats[0], ", ".join(next_cats[1:3]))
    )
    return {
        "enable": False,
        "source": "lab",
        "model": None,
        "verb_pl": "NIE WŁĄCZAJ",
        "verb_en": "DO NOT ENABLE",
        "now_pl": now_pl,
        "now_en": (
            "No lane cleared the gates. Leave the $39. Closest path is hunt for a $5 / $15 book, "
            "not more cash and not All Copy."
        ),
        "path_pl": path_pl,
        "path_en": (
            "Closest: %s (%s). Missing: %s. Look in Simulation. Do nothing in Telegram."
            % (name, lane, missing)
        ),
        "hunt_next_pl": hunt_pl,
        "hunt_next_en": (
            "Next run: deeper %s (offset 250/300), then %s. Skip Top 8, 5-min candles, esports, conc=1.0 picks."
            % (next_cats[0], ", ".join(next_cats[1:3]))
        ),
        "closest": {"name": name, "lane": lane, "missing": missing},
        "next_cats": next_cats,
        "extra_offsets": extra,
        "dead_pl": dead[:4],
        "dead_en": dead[:4],
    }


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


def clamp_plan(data: dict[str, Any], fallback: dict[str, Any]) -> dict[str, Any]:
    out = dict(fallback)
    out["enable"] = False
    out["verb_pl"] = "NIE WŁĄCZAJ"
    out["verb_en"] = "DO NOT ENABLE"
    for key in (
        "now_pl", "now_en", "path_pl", "path_en",
        "hunt_next_pl", "hunt_next_en",
    ):
        val = data.get(key)
        if isinstance(val, str) and val.strip():
            out[key] = val.strip()[:420]
    cats: list[str] = []
    for c in data.get("next_cats") or []:
        c = str(c).upper()
        if c in CATS and c not in cats:
            cats.append(c)
    if cats:
        for c in CATS:
            if c not in cats:
                cats.append(c)
        out["next_cats"] = cats
    extra: dict[str, list[int]] = {}
    raw = data.get("extra_offsets") or {}
    if isinstance(raw, dict):
        for cat, offs in raw.items():
            cat = str(cat).upper()
            if cat not in CATS or not isinstance(offs, list):
                continue
            nums = []
            for x in offs:
                try:
                    n = int(x)
                except (TypeError, ValueError):
                    continue
                if 50 <= n <= 400 and n not in nums:
                    nums.append(n)
            if nums:
                extra[cat] = nums[:4]
    if extra:
        out["extra_offsets"] = extra
    closest = data.get("closest")
    if isinstance(closest, dict) and closest.get("name"):
        out["closest"] = {
            "name": str(closest.get("name"))[:40],
            "lane": str(closest.get("lane") or out["closest"].get("lane") or "")[:20],
            "missing": str(closest.get("missing") or "")[:160],
        }
    dead = data.get("dead_pl") or data.get("dead") or []
    if isinstance(dead, list) and dead:
        out["dead_pl"] = [str(x)[:80] for x in dead[:4]]
        out["dead_en"] = [str(x)[:80] for x in (data.get("dead_en") or dead)[:4]]
    low = (out["now_pl"] + out["path_pl"] + out["hunt_next_pl"]).lower()
    banned = ("włącz copy", "turn on copy", "turn on all", "dokup", "deposit more", "kup ")
    if any(b in low for b in banned):
        return fallback
    return out


def call_claude(payload: dict[str, Any]) -> tuple[dict[str, Any] | None, str | None]:
    key = (os.environ.get("ANTHROPIC_API_KEY") or "").strip()
    if not key:
        return None, None
    body_user = json.dumps(payload, ensure_ascii=False)[:12000]
    user = (
        "Tape below. Reply JSON keys: now_pl, now_en, path_pl, path_en, "
        "hunt_next_pl, hunt_next_en, closest{name,lane,missing}, next_cats, "
        "extra_offsets, dead_pl, dead_en. enable must be false.\n\n" + body_user
    )
    last_err = None
    for model in MODELS:
        req_body = json.dumps({
            "model": model,
            "max_tokens": 700,
            "temperature": 0.2,
            "system": SYSTEM,
            "messages": [{"role": "user", "content": user}],
        }).encode()
        req = urllib.request.Request(
            "https://api.anthropic.com/v1/messages",
            data=req_body,
            headers={
                "x-api-key": key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=45) as resp:
                blob = json.loads(resp.read().decode())
        except urllib.error.HTTPError as exc:
            last_err = "http %s %s" % (exc.code, model)
            err_body = exc.read().decode()[:200]
            if exc.code in (400, 404, 422):
                continue
            return None, last_err + " " + err_body
        except Exception as exc:  # noqa: BLE001
            return None, str(exc)[:160]
        bits = []
        for block in blob.get("content") or []:
            if isinstance(block, dict) and block.get("type") == "text":
                bits.append(block.get("text") or "")
        parsed = parse_model_json("\n".join(bits))
        if parsed:
            parsed["_model"] = model
            return parsed, None
        last_err = "bad json from " + model
    return None, last_err


def main() -> int:
    hunt = load_json(HUNT)
    snap = load_json(SNAP)
    fallback = numeric_path(hunt, snap)
    payload = {
        "cash_usd": snap.get("cash_usd") or 39.08,
        "hole_usd": snap.get("last_live_pnl_usd") or -13.58,
        "watch_not_enable": snap.get("watch_not_enable") or [],
        "hunt": compact_hunt(hunt),
    }
    parsed, err = call_claude(payload)
    if parsed:
        out = clamp_plan(parsed, fallback)
        out["source"] = "claude"
        out["model"] = parsed.get("_model")
    else:
        out = fallback
        out["source"] = "lab"
        if err:
            out["error"] = err[:160]
    out["updated_at"] = datetime.now(timezone.utc).isoformat()
    out["hunt_updated_at"] = hunt.get("updated_at")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, indent=2, ensure_ascii=False) + "\n")
    print("wrote", OUT, "source", out.get("source"), "cats", out.get("next_cats"), file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
