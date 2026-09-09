#!/usr/bin/env python3
"""Stratified non-sport screen so sports whales don't eat the sim budget."""

from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path

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
CATS = ["POLITICS", "FINANCE", "ECONOMICS", "CRYPTO", "CULTURE", "TECH"]
OUT = ROOT / "data" / "deep_screen_nonsport.json"
SETTINGS = CopySettings(total_spend_limit_usd=15.0)


def addr(row: dict) -> str:
    return str(row.get("proxyWallet") or row.get("user") or row.get("address") or "").lower()


async def fetch_lb(client: httpx.AsyncClient, category: str, period: str, offset: int = 0):
    resp = await client.get(
        f"{DATA_API}/v1/leaderboard",
        params={"category": category, "timePeriod": period, "orderBy": "PNL", "limit": 50, "offset": offset},
        timeout=30.0,
    )
    resp.raise_for_status()
    data = resp.json()
    return data if isinstance(data, list) else []


def window_sim(trades, closed, days: int):
    cutoff = time.time() - days * 86400
    recent = [t for t in trades if trade_ts(t) >= cutoff]
    if not recent:
        return {"pnl": 0.0, "wins": 0, "losses": 0, "copies": 0, "win_pct": 0.0}
    positions, _ = simulate_copies(recent, SETTINGS, closed)
    pnl, wins, losses, _, _ = settle_positions(positions, closed)
    settled = wins + losses
    return {
        "pnl": pnl,
        "wins": wins,
        "losses": losses,
        "copies": len(positions),
        "win_pct": round(100 * wins / max(settled, 1), 0),
    }


async def main() -> None:
    universe = {}
    async with httpx.AsyncClient(headers={"Accept": "application/json", "User-Agent": "Mozilla/5.0 research"}) as client:
        for cat in CATS:
            rows = await fetch_lb(client, cat, "MONTH", 0)
            await asyncio.sleep(0.2)
            rows += await fetch_lb(client, cat, "MONTH", 50)
            print(f"{cat} {len(rows)}", file=sys.stderr)
            for row in rows:
                w = addr(row)
                if not w.startswith("0x"):
                    continue
                rec = universe.setdefault(w, {"wallet": w, "username": row.get("userName") or w[:10], "cat": set(), "pnl": 0.0})
                rec["cat"].add(cat)
                rec["pnl"] = max(rec["pnl"], float(row.get("pnl") or 0))
            await asyncio.sleep(0.15)

        results = []
        for rec in universe.values():
            rec["cat"] = sorted(rec["cat"])
        # history
        ok = []
        for rec in universe.values():
            if rec["pnl"] > 250_000:
                continue
            w = rec["wallet"]
            try:
                trades = await fetch_trades(client, w, limit=400)
            except Exception:
                await asyncio.sleep(0.3)
                continue
            ts = [trade_ts(t) for t in trades if trade_ts(t)]
            if not ts:
                continue
            days = (max(ts) - min(ts)) / 86400
            tpd = len(trades) / max(days, 1)
            if days < 60 or tpd > 15:
                await asyncio.sleep(0.22)
                continue
            mix = {}
            for t in trades[:200]:
                d = classify_domain(str(t.get("title") or ""))
                mix[d] = mix.get(d, 0) + 1
            rec["history_days"] = round(days, 1)
            rec["tpd"] = round(tpd, 1)
            rec["domain"] = max(mix, key=mix.get) if mix else "other"
            ok.append(rec)
            await asyncio.sleep(0.22)
        print(f"nonsport 60d survivors {len(ok)} / {len(universe)}", file=sys.stderr)
        # sim up to 6 per category
        picked = []
        by_cat: dict[str, list] = {c: [] for c in CATS}
        for rec in sorted(ok, key=lambda r: -r["pnl"]):
            for c in rec["cat"]:
                if len(by_cat[c]) < 6:
                    by_cat[c].append(rec)
                    break
        for recs in by_cat.values():
            picked.extend(recs)
        # unique
        seen = set()
        uniq = []
        for rec in picked:
            if rec["wallet"] in seen:
                continue
            seen.add(rec["wallet"])
            uniq.append(rec)
        print("simming", len(uniq), file=sys.stderr)
        for rec in uniq:
            w = rec["wallet"]
            bt = await run_isolated_backtest(w, username=rec["username"], settings=SETTINGS)
            await asyncio.sleep(0.3)
            async with httpx.AsyncClient(headers={"Accept": "application/json"}) as c2:
                trades = await fetch_trades(c2, w, limit=500)
                closed = await fetch_closed_positions(c2, w, limit=400)
            w60 = window_sim(trades, closed, 60)
            w90 = window_sim(trades, closed, 90)
            row = {
                **rec,
                "sim_pnl": bt.sim_pnl,
                "sim_copies": bt.copy_count,
                "sim_wins": bt.wins,
                "sim_losses": bt.losses,
                "max_yes_no": bt.max_outcome_exposure,
                "error": bt.error,
                "w60": w60,
                "w90": w90,
            }
            results.append(row)
            print(
                f"{rec['username'][:18]:18} {rec['domain']:7} {rec['cat']} "
                f"d={rec['history_days']:.0f} 60 ${w60['pnl']:+.1f} 90 ${w90['pnl']:+.1f} copies={bt.copy_count}",
                file=sys.stderr,
            )
            await asyncio.sleep(0.25)

    OUT.write_text(json.dumps({"n": len(results), "rows": results}, indent=2, default=str))
    print("wrote", OUT, file=sys.stderr)


if __name__ == "__main__":
    asyncio.run(main())
