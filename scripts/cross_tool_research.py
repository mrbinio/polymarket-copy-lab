#!/usr/bin/env python3
"""Independent-tool cross-check + concentration + ALL-time mid-tier screen.

CopyGrade HTTP is public (no key). PolyCop stays the only executor.
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
from collections import defaultdict
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
OUT = ROOT / "data" / "cross_tool_research.json"
SETTINGS = CopySettings(total_spend_limit_usd=15.0)

TOP8 = {
    "disputequant": "0x6e131d6c61216b4a5ec571db332ad3226e7fa801",
    "0xwise": "0x0e604be17c231a33dc01e38a722c7fe3984e3bad",
    "invorser": "0x84fbaa0a94c4112b871e3ca039d1d2eb5603675e",
    "PotatoKotato": "0xa7cc00c563726032007221e827bbf7fdb6d7644e",
    "Bertapotamous": "0x134a63b764ac7b008356e8db1857db94e6b09e42",
    "Weaseloftheweek": "0x5dab5ed9691fab220535891d9c7f5c28eed322e1",
    "0x903221b1": "0x903221b1098263779ae19486028c844e8c338717",
    "GrokEnjoyer": "0x92e53057ef2872722a53fe807245bd387e1defee",
}

# Independent shortlist we resolved from CopyGrade URLs / Merlin
EXTERNAL = {
    "PoppyG": "0x5b7fc994c653072b721669229ea2bed937065fea",  # CopyGrade #1 — 5m BTC
}


def window_sim(trades, closed, days: int) -> dict[str, Any]:
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


def concentration(settled_rows: list[dict]) -> dict[str, Any]:
    by_m: dict[str, float] = defaultdict(float)
    n_m: dict[str, int] = defaultdict(int)
    for row in settled_rows:
        if row.get("pnl") is None:
            continue
        m = str(row.get("market") or "?")[:80]
        by_m[m] += float(row["pnl"])
        n_m[m] += 1
    if not by_m:
        return {"markets": 0, "top_share": 0.0, "top_market": None, "top3": []}
    total = sum(abs(v) for v in by_m.values()) or 1.0
    ranked = sorted(by_m.items(), key=lambda kv: -abs(kv[1]))
    top = ranked[0]
    return {
        "markets": len(by_m),
        "top_share": round(abs(top[1]) / total, 2),
        "top_market": top[0],
        "top_pnl": round(top[1], 2),
        "top3": [{"market": k, "pnl": round(v, 2), "n": n_m[k]} for k, v in ranked[:3]],
    }


def candle_flag(trades: list) -> dict[str, Any]:
    titles = [str(t.get("title") or "") for t in trades[:400]]
    n = len(titles) or 1
    five_min = sum(1 for x in titles if "Up or Down" in x or "5:00PM" in x or "5-min" in x.lower() or "5m" in x.lower())
    updown = sum(1 for x in titles if "Up or Down" in x)
    return {
        "up_or_down_share": round(updown / n, 2),
        "likely_candle_bot": updown / n > 0.4,
        "sample_titles": titles[:3],
    }


async def grade(client: httpx.AsyncClient, wallet: str) -> dict[str, Any]:
    try:
        resp = await client.get(f"{CG}/{wallet}", timeout=20.0)
        if resp.status_code == 404:
            return {"status": "not_indexed"}
        resp.raise_for_status()
        payload = resp.json()
        data = payload.get("data") or payload
        return {"status": "ok", **{k: data.get(k) for k in (
            "handle", "score", "verdict", "label", "edge90d", "edgeReal"
        )}, "farming": data.get("farming"), "subScores": data.get("subScores")}
    except Exception as exc:  # noqa: BLE001
        return {"status": "error", "error": str(exc)[:120]}


async def fetch_lb(client: httpx.AsyncClient, category: str, period: str, offset: int = 0):
    resp = await client.get(
        f"{DATA_API}/v1/leaderboard",
        params={"category": category, "timePeriod": period, "orderBy": "PNL", "limit": 50, "offset": offset},
        timeout=30.0,
    )
    resp.raise_for_status()
    data = resp.json()
    return data if isinstance(data, list) else []


def addr(row: dict) -> str:
    return str(row.get("proxyWallet") or row.get("user") or row.get("address") or "").lower()


async def profile_wallet(client: httpx.AsyncClient, wallet: str, username: str) -> dict[str, Any]:
    bt = await run_isolated_backtest(wallet, username=username, settings=SETTINGS)
    trades = await fetch_trades(client, wallet, limit=500)
    closed = await fetch_closed_positions(client, wallet, limit=400)
    _, _, _, _, settled = settle_positions(
        simulate_copies(trades, SETTINGS, closed)[0], closed
    )
    return {
        "wallet": wallet,
        "username": username,
        "history_days": bt.history_days,
        "tpd": round(bt.trades_per_day, 1),
        "sim_pnl": bt.sim_pnl,
        "sim_copies": bt.copy_count,
        "sim_wins": bt.wins,
        "sim_losses": bt.losses,
        "max_yes_no": bt.max_outcome_exposure,
        "error": bt.error,
        "w60": window_sim(trades, closed, 60),
        "w90": window_sim(trades, closed, 90),
        "concentration": concentration(settled),
        "candle": candle_flag(trades),
        "domain": classify_domain(str((trades[0] or {}).get("title") or "")) if trades else "?",
    }


async def main() -> None:
    sport = json.loads((ROOT / "data" / "deep_screen.json").read_text())
    ns = json.loads((ROOT / "data" / "deep_screen_nonsport.json").read_text())

    candidates: dict[str, dict[str, Any]] = {}
    for r in sport.get("passed_60_90") or []:
        candidates[r["wallet"]] = {"username": r.get("username"), "source": "lab_sport_60_90"}
    for r in ns.get("rows") or []:
        w60 = (r.get("w60") or {}).get("pnl", 0)
        w90 = (r.get("w90") or {}).get("pnl", 0)
        if w60 > 0 and w90 > 0:
            candidates[r["wallet"]] = {"username": r.get("username"), "source": "lab_nonsport_60_90"}
    for name, w in TOP8.items():
        candidates.setdefault(w, {"username": name, "source": "old_top8"})
    for name, w in EXTERNAL.items():
        candidates[w] = {"username": name, "source": "copygrade_url"}

    method = {
        "pulled_at": datetime.now(timezone.utc).isoformat(),
        "tools_queried": [
            "Polymarket Data API leaderboard MONTH+ALL",
            "Merlin /api/leaderboard",
            "CopyGrade public GET /api/v1/wallets/{addr} (MCP-equivalent, no key)",
            "CopyGrade website shortlist Sep 2026",
            "PolyTrackers OpenAPI (auth required — not used for execution)",
            "FrenFlow/Poly Syncer public APIs — no open leaderboard endpoint",
        ],
        "sim_filters": {
            "fixed_usd": 5,
            "ignore_below": 20,
            "spend_cap": 15,
            "min_history_days": 60,
        },
    }

    grades = {}
    profiles = {}
    all_time_sim = []

    headers = {"Accept": "application/json", "User-Agent": "Mozilla/5.0 copy-lab-research"}
    async with httpx.AsyncClient(headers=headers) as client:
        print("CopyGrade grades…", file=sys.stderr)
        for w, meta in candidates.items():
            g = await grade(client, w)
            grades[w] = {**meta, **g}
            print(f"  CG {meta['username'][:18]:18} {g.get('status')} {g.get('score')} {g.get('label')} farm={g.get('farming')}", file=sys.stderr)
            await asyncio.sleep(0.35)

        print("Lab profiles + concentration…", file=sys.stderr)
        # Deep-profile: external + nonsport passers + sport passers (not already in first 40 dump)
        to_profile = list(EXTERNAL.items()) + [
            (r.get("username"), r["wallet"])
            for r in (ns.get("rows") or [])
            if (r.get("w60") or {}).get("pnl", 0) > 0 and (r.get("w90") or {}).get("pnl", 0) > 0
        ] + [
            (r.get("username"), r["wallet"])
            for r in (sport.get("passed_60_90") or [])
        ]
        seen = set()
        for username, w in to_profile:
            if w in seen:
                continue
            seen.add(w)
            try:
                profiles[w] = await profile_wallet(client, w, username or w[:10])
                p = profiles[w]
                print(
                    f"  {username[:18]:18} d={p['history_days']:.0f} 60={p['w60']['pnl']:+.1f} "
                    f"conc={p['concentration']['top_share']} candle={p['candle']['likely_candle_bot']}",
                    file=sys.stderr,
                )
            except Exception as exc:  # noqa: BLE001
                profiles[w] = {"error": str(exc)[:120], "username": username}
            await asyncio.sleep(0.3)

        print("ALL-time mid-tier (offset 50+100)…", file=sys.stderr)
        cats = ["POLITICS", "FINANCE", "ECONOMICS", "CRYPTO", "CULTURE", "TECH", "SPORTS"]
        universe: dict[str, dict] = {}
        for cat in cats:
            for offset in (50, 100):
                try:
                    rows = await fetch_lb(client, cat, "ALL", offset)
                except Exception as exc:  # noqa: BLE001
                    print(f"  LB fail {cat} ALL {offset}: {exc}", file=sys.stderr)
                    continue
                print(f"  ALL {cat} off={offset} n={len(rows)}", file=sys.stderr)
                for row in rows:
                    w = addr(row)
                    if not w.startswith("0x"):
                        continue
                    pnl = float(row.get("pnl") or 0)
                    if pnl > 400_000:
                        continue
                    rec = universe.setdefault(
                        w,
                        {"wallet": w, "username": row.get("userName") or w[:10], "cat": set(), "pnl": 0.0},
                    )
                    rec["cat"].add(cat)
                    rec["pnl"] = max(rec["pnl"], pnl)
                await asyncio.sleep(0.2)

        ok = []
        for rec in universe.values():
            rec["cat"] = sorted(rec["cat"])
            try:
                trades = await fetch_trades(client, rec["wallet"], limit=400)
            except Exception:
                await asyncio.sleep(0.2)
                continue
            ts = [trade_ts(t) for t in trades if trade_ts(t)]
            if not ts:
                continue
            days = (max(ts) - min(ts)) / 86400
            tpd = len(trades) / max(days, 1)
            rec["history_days"] = round(days, 1)
            rec["tpd"] = round(tpd, 1)
            if days < 90 or tpd > 12:
                await asyncio.sleep(0.18)
                continue
            mix: dict[str, int] = {}
            for t in trades[:200]:
                d = classify_domain(str(t.get("title") or ""))
                mix[d] = mix.get(d, 0) + 1
            rec["domain"] = max(mix, key=mix.get) if mix else "other"
            ok.append(rec)
            await asyncio.sleep(0.18)
        print(f"ALL-time 90d survivors {len(ok)} / {len(universe)}", file=sys.stderr)

        picked = []
        by_cat: dict[str, list] = {c: [] for c in cats}
        for rec in sorted(ok, key=lambda r: -r["pnl"]):
            for c in rec["cat"]:
                if len(by_cat[c]) < 4:
                    by_cat[c].append(rec)
                    break
        seenw = set()
        for recs in by_cat.values():
            for rec in recs:
                if rec["wallet"] in seenw:
                    continue
                seenw.add(rec["wallet"])
                picked.append(rec)

        print(f"simming ALL-time {len(picked)}", file=sys.stderr)
        for rec in picked:
            w = rec["wallet"]
            try:
                row = await profile_wallet(client, w, rec["username"])
                row["source"] = "all_time_midtier"
                row["lb_cats"] = rec["cat"]
                row["lb_pnl"] = rec["pnl"]
                all_time_sim.append(row)
                print(
                    f"  {rec['username'][:18]:18} {rec['cat']} d={row['history_days']:.0f} "
                    f"60={row['w60']['pnl']:+.1f} 90={row['w90']['pnl']:+.1f} copies={row['sim_copies']}",
                    file=sys.stderr,
                )
            except Exception as exc:  # noqa: BLE001
                all_time_sim.append({"username": rec["username"], "wallet": w, "error": str(exc)[:120]})
            await asyncio.sleep(0.25)

            # grade ALL-time picks too (budget: CopyGrade 20/min)
            if w not in grades:
                g = await grade(client, w)
                grades[w] = {"username": rec["username"], "source": "all_time_midtier", **g}
                await asyncio.sleep(0.3)

    payload = {
        "method": method,
        "copygrade_grades": grades,
        "profiles": profiles,
        "all_time_sim": all_time_sim,
        "poppyg_note": "CopyGrade #1 most-traded markets are Bitcoin 5-minute Up/Down candles — latency edge, not PolyCop-copyable.",
    }
    OUT.write_text(json.dumps(payload, indent=2, default=str))
    print("wrote", OUT, file=sys.stderr)


if __name__ == "__main__":
    asyncio.run(main())
