#!/usr/bin/env python3
"""Eval monitor — run before any roster/bankroll decision.

Checks:
- Session settled + open PnL (in-window)
- Open position count and worst drawdown
- Per-wallet domain mix + fresh isolated backtest sim
- Cross-wallet market overlap (last 30 trades each)

Usage:
    python scripts/monitor_eval.py
    python scripts/monitor_eval.py --genome configs/v1.0.0.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dashboard.backtest_engine import CopySettings, run_isolated_backtest
from dashboard.polymarket_api import (
    compute_live_account,
    fetch_all_closed_since,
    fetch_open_positions,
    open_position_summary,
    settlement_from_closed,
)

DATA_API = "https://data-api.polymarket.com"
MONITOR_PATH = ROOT / "configs" / "monitor.json"
SESSION_PATH = ROOT / "data" / "session_state.json"
OUT_PATH = ROOT / "data" / "monitor_snapshot.json"

ESPORT_KW = re.compile(
    r"lol|league|dota|cs2|csgo|counter-strike|valorant|esport|bo3|bo5|lck|lpl|"
    r"iem|blast|game \d|handicap|vct",
    re.I,
)
SPORT_KW = re.compile(
    r"fc | vs\.|win on 20|ufc|nba|nfl|mlb|open|atp|wta|premier|draw\?",
    re.I,
)


def classify(title: str) -> str:
    if ESPORT_KW.search(title):
        return "esport"
    if SPORT_KW.search(title):
        return "sport"
    return "other"


def load_json(path: Path) -> dict:
    return json.loads(path.read_text())


async def fetch_trades(client: httpx.AsyncClient, wallet: str, limit: int = 200) -> list[dict]:
    resp = await client.get(f"{DATA_API}/trades", params={"user": wallet, "limit": limit}, timeout=30.0)
    resp.raise_for_status()
    data = resp.json()
    return data if isinstance(data, list) else []


async def audit_wallet(client: httpx.AsyncClient, name: str, address: str, settings: CopySettings) -> dict:
    trades = await fetch_trades(client, address)
    cats = Counter(classify(str(t.get("title") or "")) for t in trades[:200])
    bt = await run_isolated_backtest(address, username=name, settings=settings)
    recent_titles = [str(t.get("title") or "") for t in trades[:30] if t.get("title")]
    return {
        "name": name,
        "address": address,
        "domain_mix_last200": dict(cats),
        "esport_pct": round(100 * cats.get("esport", 0) / max(sum(cats.values()), 1), 0),
        "isolated_sim_pnl": bt.sim_pnl,
        "isolated_copy_count": bt.copy_count,
        "isolated_wl": f"{bt.wins}/{bt.losses}",
        "trades_per_day": bt.trades_per_day,
        "recent_titles": recent_titles[:5],
    }


def overlap_report(wallets: list[dict]) -> list[dict]:
    title_to_names: dict[str, set[str]] = defaultdict(set)
    for w in wallets:
        for t in w.get("recent_titles") or []:
            title_to_names[t].add(w["name"])
    overlaps = [
        {"market": t, "wallets": sorted(names)}
        for t, names in title_to_names.items()
        if len(names) >= 2
    ]
    overlaps.sort(key=lambda x: (-len(x["wallets"]), x["market"]))
    return overlaps[:20]


def alerts(snapshot: dict) -> list[str]:
    out: list[str] = []
    sess = snapshot["session"]
    if sess["session_total"] < -5:
        out.append(f"CRITICAL: session total ${sess['session_total']:.2f} — consider Stop All Copy")
    if sess["open_count"] >= 5:
        out.append(f"WARN: {sess['open_count']} open positions — high correlation risk")
    if sess["open_pnl"] < -10:
        out.append(f"WARN: open unrealized ${sess['open_pnl']:.2f}")
    for w in snapshot["wallets"]:
        if w["isolated_sim_pnl"] <= 0 and w["isolated_copy_count"] >= 5:
            out.append(f"WARN: {w['name']} isolated sim ${w['isolated_sim_pnl']:.2f} — weak under our filters")
        if w["esport_pct"] >= 85:
            out.append(f"INFO: {w['name']} is {w['esport_pct']:.0f}% esport recently")
    if len(snapshot.get("overlaps") or []) >= 2:
        out.append(f"WARN: {len(snapshot['overlaps'])} shared markets across active wallets")
    return out


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--genome", default="configs/v1.0.0.json")
    args = parser.parse_args()

    genome = load_json(ROOT / args.genome)
    monitor = load_json(MONITOR_PATH)
    session = load_json(SESSION_PATH)
    wallet = monitor.get("poly_wallet", "")
    anchor = session.get("anchor_total_usd")
    since = float(session.get("started_at") or session.get("anchor_set_at") or 0)
    eval_baseline = session.get("eval_baseline_usd") or anchor
    cs = genome.get("copy_settings", {})
    settings = CopySettings(
        fixed_amount_usd=cs.get("fixed_amount_usd", 5.0),
        ignore_below_usd=cs.get("ignore_below_usd", 20.0),
        min_price=cs.get("min_price", 0.15),
        max_price=cs.get("max_price", 0.85),
        max_per_trade_usd=cs.get("max_per_trade_usd", 5.0),
        max_per_yes_no_usd=cs.get("max_per_yes_no_usd", 5.0),
        total_spend_limit_usd=cs.get("total_spend_limit_usd", 30.0),
    )

    closed_raw = await fetch_all_closed_since(wallet, since)
    closed = [settlement_from_closed(r) for r in closed_raw]
    settled_net = round(sum(r["pnl"] for r in closed), 2)
    open_rows = await fetch_open_positions(wallet)
    open_summ = [open_position_summary(p) for p in open_rows]
    open_pnl = round(sum(p["pnl"] for p in open_summ), 2)

    live = None
    session_total = settled_net + open_pnl
    cashflow_net = None
    if anchor is not None:
        live = await compute_live_account(
            wallet,
            anchor_total_usd=float(eval_baseline or anchor),
            since_ts=since,
        )
        session_total = live["session_pnl_usd"]
        cashflow_net = live["cashflow"]["net_cashflow_usd"]

    wallet_audits: list[dict] = []
    async with httpx.AsyncClient(headers={"Accept": "application/json"}) as client:
        for item in genome.get("target_wallets", []):
            w = await audit_wallet(client, item.get("username") or "wallet", item["address"], settings)
            wallet_audits.append(w)
            await asyncio.sleep(0.35)

    snapshot = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "genome": genome.get("version"),
        "eval_hours": round((time.time() - since) / 3600, 1) if since else None,
        "session": {
            "active": session.get("active"),
            "account_total_usd": live["account_total_usd"] if live else None,
            "settled_wins_only_usd": settled_net,
            "cashflow_net_usd": cashflow_net,
            "resolved_count": len(closed),
            "open_count": len(open_summ),
            "open_pnl": open_pnl,
            "session_total": round(session_total, 2),
            "open_positions": open_summ,
            "recent_settled": closed[:8],
        },
        "wallets": wallet_audits,
        "overlaps": overlap_report(wallet_audits),
        "alerts": [],
    }
    snapshot["alerts"] = alerts(snapshot)

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(snapshot, indent=2))

    print(f"Snapshot → {OUT_PATH}")
    print(f"Session: account ${snapshot['session'].get('account_total_usd')} | PnL ${snapshot['session']['session_total']:+.2f} | cashflow ${cashflow_net}")
    print(f"Open positions: {len(open_summ)} | Eval hours: {snapshot['eval_hours']}")
    if snapshot["alerts"]:
        print("\nALERTS:")
        for a in snapshot["alerts"]:
            print(f"  • {a}")
    else:
        print("\nNo alerts.")


if __name__ == "__main__":
    asyncio.run(main())
