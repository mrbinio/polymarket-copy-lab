#!/usr/bin/env python3
"""Update ops/data/snapshot.json cash_usd from Polymarket activity.

Does not talk to PolyCop. Does not enable copy. Never prints the wallet.
GitHub: set secret POLYCOP_WALLET. Local: configs/monitor.json poly_wallet.
"""

from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SNAP = ROOT / "ops" / "data" / "snapshot.json"
MONITOR = ROOT / "configs" / "monitor.json"
DATA_API = "https://data-api.polymarket.com"


def load_wallet() -> str | None:
    env = (os.environ.get("POLYCOP_WALLET") or "").strip()
    if env.startswith("0x") and len(env) == 42:
        return env.lower()
    if MONITOR.exists():
        raw = (json.loads(MONITOR.read_text()).get("poly_wallet") or "").strip()
        if raw.startswith("0x") and len(raw) == 42 and "YOUR" not in raw.upper():
            return raw.lower()
    return None


def get_json(path: str, params: dict) -> object:
    url = DATA_API + path + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(
        url,
        headers={"Accept": "application/json", "User-Agent": "copy-lab-ops"},
    )
    with urllib.request.urlopen(req, timeout=25) as resp:
        return json.loads(resp.read().decode())


def parse_ts(val: object) -> float:
    if val is None:
        return 0.0
    if isinstance(val, (int, float)):
        n = float(val)
        return n / 1000.0 if n > 1e12 else n
    if isinstance(val, str):
        try:
            return datetime.fromisoformat(val.replace("Z", "+00:00")).timestamp()
        except ValueError:
            return 0.0
    return 0.0


def activity_since(wallet: str, since_ts: float) -> list[dict]:
    rows: list[dict] = []
    offset = 0
    while offset <= 4000:
        batch = get_json("/activity", {"user": wallet, "limit": 100, "offset": offset})
        if not isinstance(batch, list) or not batch:
            break
        stop = False
        for row in batch:
            ts = parse_ts(row.get("timestamp"))
            if ts and ts < since_ts:
                stop = True
                break
            rows.append(row)
        if stop or len(batch) < 100:
            break
        offset += 100
    return rows


def net_cashflow(rows: list[dict]) -> float:
    buys = sells = redeems = extra = 0.0
    for row in rows:
        kind = str(row.get("type") or "")
        side = str(row.get("side") or "")
        try:
            usdc = float(row.get("usdcSize") or 0)
        except (TypeError, ValueError):
            usdc = 0.0
        if kind == "TRADE" and side == "BUY":
            buys += usdc
        elif kind == "TRADE" and side == "SELL":
            sells += usdc
        elif kind == "REDEEM":
            redeems += usdc
        elif kind in ("DEPOSIT", "REWARD", "CONVERSION"):
            extra += usdc
        elif kind in ("WITHDRAW", "SPLIT"):
            extra -= usdc
    return round(redeems + sells + extra - buys, 2)


def positions_value(wallet: str) -> float:
    data = get_json("/value", {"user": wallet})
    if isinstance(data, list) and data:
        try:
            return round(float(data[0].get("value") or 0), 2)
        except (TypeError, ValueError):
            return 0.0
    if isinstance(data, dict):
        try:
            return round(float(data.get("value") or 0), 2)
        except (TypeError, ValueError):
            return 0.0
    return 0.0


def main() -> int:
    try:
        return refresh()
    except Exception as exc:
        print("cash fail", type(exc).__name__)
        return 0


def refresh() -> int:
    wallet = load_wallet()
    if not wallet:
        print("cash skip (no POLYCOP_WALLET / monitor.json)")
        return 0
    if not SNAP.exists():
        print("cash skip (no snapshot.json)")
        return 0
    snap = json.loads(SNAP.read_text())
    try:
        stamp = float(snap.get("cash_stamp_usd") if snap.get("cash_stamp_usd") is not None else snap.get("cash_usd") or 0)
    except (TypeError, ValueError):
        stamp = 0.0
    snap["cash_stamp_usd"] = stamp
    if not snap.get("cash_stamp_at"):
        snap["cash_stamp_at"] = snap.get("generated_at")
    since_ts = parse_ts(snap.get("cash_stamp_at")) or (
        datetime.now(timezone.utc).timestamp() - 14 * 86400
    )
    rows = activity_since(wallet, since_ts)
    net = net_cashflow(rows)
    pos = positions_value(wallet)
    total = round(stamp + net + pos, 2)
    now = datetime.now(timezone.utc).isoformat()
    snap["cash_usd"] = total
    snap["positions_usd"] = pos
    snap["cash_as_of"] = now
    snap["cash_source"] = "polymarket"
    SNAP.write_text(json.dumps(snap, indent=2) + "\n")
    print(
        "cash",
        total,
        "pos",
        pos,
        "net",
        net,
        "events",
        len(rows),
        "source polymarket",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
