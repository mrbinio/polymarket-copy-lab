#!/usr/bin/env python3
"""Keep hunting wallets the first pass missed. Writes ops/data/hunt.json as it goes."""

from __future__ import annotations

import asyncio
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dashboard.backtest_engine import (  # noqa: E402
    CopySettings,
    fetch_closed_positions,
    fetch_trades,
    run_isolated_backtest,
    settle_positions,
    simulate_copies,
)
from dashboard.backtest_engine import _ts as trade_ts  # noqa: E402
from dashboard.scout_engine import classify_domain  # noqa: E402

DATA_API = "https://data-api.polymarket.com"
CG = "https://copygrade.com/api/v1/wallets"
OUT = ROOT / "ops" / "data" / "hunt.json"
SETTINGS = CopySettings(total_spend_limit_usd=15.0)
BANNED = {
    "0x84fbaa0a94c4112b871e3ca039d1d2eb5603675e",  # invorser
    "0x5b7fc994c653072b721669229ea2bed937065fea",  # PoppyG candles
    "0xdbf642e200cfcfcf79d1a9095b86297e2ed012ca",  # Bromsloy candles
}

CATS = ["POLITICS", "CULTURE", "TECH", "FINANCE", "SPORTS", "ECONOMICS"]


def addr(row: dict) -> str:
    return str(row.get("proxyWallet") or row.get("user") or row.get("address") or "").lower()


def known_wallets() -> set[str]:
    known = set(BANNED)
    for path in (
        ROOT / "data" / "deep_screen.json",
        ROOT / "data" / "deep_screen_nonsport.json",
        ROOT / "data" / "cross_tool_research.json",
    ):
        if not path.exists():
            continue
        blob = json.loads(path.read_text())
        for key in ("all_simulated", "passed_60_90", "rows", "all_time_sim"):
            for row in blob.get(key) or []:
                w = str(row.get("wallet") or "").lower()
                if w.startswith("0x"):
                    known.add(w)
        for w in (blob.get("copygrade_grades") or {}):
            known.add(str(w).lower())
        for w in (blob.get("profiles") or {}):
            known.add(str(w).lower())
    return known


def window_sim(trades, closed, days: int) -> dict[str, Any]:
    cutoff = time.time() - days * 86400
    recent = [t for t in trades if trade_ts(t) >= cutoff]
    if not recent:
        return {"pnl": 0.0, "wins": 0, "losses": 0, "copies": 0}
    positions, _ = simulate_copies(recent, SETTINGS, closed)
    pnl, wins, losses, _, _ = settle_positions(positions, closed)
    return {"pnl": pnl, "wins": wins, "losses": losses, "copies": len(positions)}


def candle_share(trades: list) -> float:
    n = len(trades) or 1
    return sum(1 for t in trades if "Up or Down" in str(t.get("title") or "")) / n


def concentration(settled: list[dict]) -> dict[str, Any]:
    by: dict[str, float] = {}
    for row in settled:
        if row.get("pnl") is None:
            continue
        m = str(row.get("market") or "?")[:70]
        by[m] = by.get(m, 0.0) + float(row["pnl"])
    if not by:
        return {"markets": 0, "top_share": 1.0, "top_market": None}
    total = sum(abs(v) for v in by.values()) or 1.0
    top_m, top_v = max(by.items(), key=lambda kv: abs(kv[1]))
    return {"markets": len(by), "top_share": round(abs(top_v) / total, 2), "top_market": top_m}


def write_hunt(payload: dict) -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    payload["updated_at"] = datetime.now(timezone.utc).isoformat()
    OUT.write_text(json.dumps(payload, indent=2, default=str))


async def fetch_lb(client, cat, period, offset):
    resp = await client.get(
        f"{DATA_API}/v1/leaderboard",
        params={"category": cat, "timePeriod": period, "orderBy": "PNL", "limit": 50, "offset": offset},
        timeout=30.0,
    )
    resp.raise_for_status()
    data = resp.json()
    return data if isinstance(data, list) else []


async def grade(client, wallet: str) -> dict:
    try:
        resp = await client.get(f"{CG}/{wallet}", timeout=20.0)
        if resp.status_code == 404:
            return {"status": "not_indexed"}
        if resp.status_code == 429:
            return {"status": "rate_limit"}
        resp.raise_for_status()
        data = (resp.json() or {}).get("data") or {}
        return {
            "status": "ok",
            "score": data.get("score"),
            "label": data.get("label"),
            "edge_real": data.get("edgeReal"),
            "farming": data.get("farming"),
        }
    except Exception as exc:  # noqa: BLE001
        return {"status": "error", "error": str(exc)[:80]}


def score_row(row: dict) -> float:
    """Higher = better operational copy at $5 / $15 spend."""
    w60 = (row.get("w60") or {}).get("pnl") or 0
    w90 = (row.get("w90") or {}).get("pnl") or 0
    if w60 <= 0 or w90 <= 0:
        return -999
    if row.get("candle", 1) > 0.25:
        return -999
    conc = (row.get("concentration") or {}).get("top_share") or 1
    copies = row.get("sim_copies") or 0
    days = row.get("history_days") or 0
    cg = row.get("copygrade") or {}
    penalty = 0.0
    if cg.get("label") and "Avoid" in str(cg.get("label")):
        penalty += 80
    if (cg.get("farming") or {}).get("level") == "severe":
        penalty += 80
    if conc > 0.45:
        penalty += 25
    if copies < 6:
        penalty += 10
    return (w60 + w90) + min(days, 180) * 0.05 + min(copies, 15) - conc * 40 - penalty


async def main() -> None:
    prev = {}
    if OUT.exists():
        try:
            prev = json.loads(OUT.read_text())
        except Exception:
            prev = {}
    seen = known_wallets()
    hunt = {
        "status": "running",
        "goal": "Find one specialist a $39 stack can copy at $5 after 60/90d, not Top 8.",
        "checked": 0,
        "log": ["cycle start"],
        "candidates": prev.get("candidates") or [],
        "pick": prev.get("pick"),
    }
    write_hunt(hunt)

    universe: dict[str, dict] = {}
    headers = {"Accept": "application/json", "User-Agent": "Mozilla/5.0 copy-lab-hunt"}
    async with httpx.AsyncClient(headers=headers) as client:
        for cat in CATS:
            for period, offsets in (("ALL", (150, 200)), ("MONTH", (100, 150))):
                for offset in offsets:
                    try:
                        rows = await fetch_lb(client, cat, period, offset)
                    except Exception as exc:  # noqa: BLE001
                        hunt["log"].append(f"LB fail {cat} {period} {offset}: {exc}")
                        write_hunt(hunt)
                        continue
                    hunt["log"].append(f"LB {cat} {period} off={offset} n={len(rows)}")
                    write_hunt(hunt)
                    for row in rows:
                        w = addr(row)
                        if not w.startswith("0x") or w in seen or w in universe:
                            continue
                        pnl = float(row.get("pnl") or 0)
                        if pnl > 400_000:
                            continue
                        universe[w] = {
                            "wallet": w,
                            "username": row.get("userName") or w[:10],
                            "cat": cat,
                            "period": period,
                            "lb_pnl": pnl,
                        }
                    await asyncio.sleep(0.2)

        hunt["log"].append(f"new wallets to scan: {len(universe)}")
        write_hunt(hunt)

        picked_by_cat: dict[str, list] = {c: [] for c in CATS}
        for rec in sorted(universe.values(), key=lambda r: -r["lb_pnl"]):
            try:
                trades = await fetch_trades(client, rec["wallet"], limit=400)
            except Exception:
                await asyncio.sleep(0.2)
                continue
            hunt["checked"] += 1
            ts = [trade_ts(t) for t in trades if trade_ts(t)]
            if not ts:
                continue
            days = (max(ts) - min(ts)) / 86400
            tpd = len(trades) / max(days, 1)
            rec["history_days"] = round(days, 1)
            rec["tpd"] = round(tpd, 1)
            rec["candle"] = round(candle_share(trades), 2)
            if days < 75 or tpd > 12 or rec["candle"] > 0.25:
                await asyncio.sleep(0.15)
                continue
            mix: dict[str, int] = {}
            for t in trades[:200]:
                d = classify_domain(str(t.get("title") or ""))
                mix[d] = mix.get(d, 0) + 1
            rec["domain"] = max(mix, key=mix.get) if mix else "other"
            if rec["domain"] == "esport":
                await asyncio.sleep(0.12)
                continue
            cat = rec["cat"]
            if len(picked_by_cat[cat]) < 6:
                picked_by_cat[cat].append(rec)
            if hunt["checked"] % 15 == 0:
                hunt["log"].append(f"scanned {hunt['checked']}, queued {sum(len(v) for v in picked_by_cat.values())}")
                write_hunt(hunt)
            await asyncio.sleep(0.15)

        to_sim = []
        seenw = set()
        for recs in picked_by_cat.values():
            for rec in recs:
                if rec["wallet"] in seenw:
                    continue
                seenw.add(rec["wallet"])
                to_sim.append(rec)

        hunt["log"].append(f"simming {len(to_sim)}")
        write_hunt(hunt)

        candidates = []
        for rec in to_sim:
            w = rec["wallet"]
            bt = await run_isolated_backtest(w, username=rec["username"], settings=SETTINGS)
            async with httpx.AsyncClient(headers=headers) as c2:
                trades = await fetch_trades(c2, w, limit=500)
                closed = await fetch_closed_positions(c2, w, limit=400)
            pos, _ = simulate_copies(trades, SETTINGS, closed)
            _, _, _, _, settled = settle_positions(pos, closed)
            row = {
                **rec,
                "sim_pnl": bt.sim_pnl,
                "sim_copies": bt.copy_count,
                "sim_wins": bt.wins,
                "sim_losses": bt.losses,
                "max_yes_no": bt.max_outcome_exposure,
                "w60": window_sim(trades, closed, 60),
                "w90": window_sim(trades, closed, 90),
                "concentration": concentration(settled),
                "candle": round(candle_share(trades), 2),
            }
            row["copygrade"] = await grade(client, w)
            row["score"] = score_row(row)
            candidates.append(row)
            hunt["log"].append(
                f"{rec['username'][:16]:16} {rec['cat']:10} d={row['history_days']:.0f} "
                f"60={row['w60']['pnl']:+.1f} 90={row['w90']['pnl']:+.1f} "
                f"conc={row['concentration']['top_share']} cg={row['copygrade'].get('label')}"
            )
            ranked = sorted(candidates, key=lambda r: -r.get("score", -999))
            hunt["candidates"] = [
                {
                    "username": r["username"],
                    "wallet": r["wallet"],
                    "cat": r["cat"],
                    "domain": r.get("domain"),
                    "history_days": r["history_days"],
                    "w60": r["w60"]["pnl"],
                    "w90": r["w90"]["pnl"],
                    "copies": r["sim_copies"],
                    "conc": r["concentration"]["top_share"],
                    "top_market": r["concentration"]["top_market"],
                    "candle": r["candle"],
                    "copygrade": r["copygrade"],
                    "score": round(r["score"], 1),
                }
                for r in ranked
                if r.get("score", -999) > -100
            ][:12]
            write_hunt(hunt)
            await asyncio.sleep(0.3)

    ranked = [r for r in sorted(candidates, key=lambda r: -r.get("score", -999)) if r.get("score", -999) > -50]
    hunt["status"] = "done"
    hunt["pick"] = hunt["candidates"][0] if hunt["candidates"] else None
    hunt["log"].append("hunt finished")
    write_hunt(hunt)
    print("wrote", OUT, "pick", hunt.get("pick"), file=sys.stderr)


if __name__ == "__main__":
    asyncio.run(main())
