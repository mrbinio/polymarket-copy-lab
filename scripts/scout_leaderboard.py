#!/usr/bin/env python3
"""Scout Polymarket leaderboard for copy-trading candidates.

Process (from friend's guide):
1. Pull 30d profit leaderboard, focus mid-tier (not #1 whales).
2. For each candidate, pull recent trades and apply OUR copy filters:
   - trade value >= $20 (PolyCop 'ignore below')
   - price 0.15-0.85 (odds band)
3. Reject ultra-HFT (PolyCop can't keep up) and tiny samples.
4. Rank by copyable flow + consistency, print top candidates.

Usage:
    python scripts/scout_leaderboard.py [--limit 100] [--top 10]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

DATA_API = "https://data-api.polymarket.com"

# Copy filters (mirror PolyCop settings)
MIN_TRADE_USD = 20.0
MIN_PRICE = 0.15
MAX_PRICE = 0.85

# Candidate filters
MID_TIER_MIN_PNL = 2_000      # skip noise accounts
MID_TIER_MAX_PNL = 150_000    # skip mega whales
MAX_TRADES_PER_DAY = 40       # skip ultra-HFT
MIN_COPYABLE_TRADES = 10      # need sample size
TRADES_FETCH_LIMIT = 500


async def fetch_leaderboard(client: httpx.AsyncClient, limit: int) -> list[dict[str, Any]]:
    resp = await client.get(
        f"{DATA_API}/v1/leaderboard",
        params={"window": "30d", "limit": limit},
        timeout=30.0,
    )
    resp.raise_for_status()
    data = resp.json()
    return data if isinstance(data, list) else []


async def fetch_trades(client: httpx.AsyncClient, wallet: str) -> list[dict[str, Any]]:
    resp = await client.get(
        f"{DATA_API}/trades",
        params={"user": wallet, "limit": TRADES_FETCH_LIMIT},
        timeout=30.0,
    )
    resp.raise_for_status()
    data = resp.json()
    return data if isinstance(data, list) else []


def trade_usd(trade: dict[str, Any]) -> float:
    try:
        size = float(trade.get("size", 0))
        price = float(trade.get("price", 0))
        return size * price
    except (TypeError, ValueError):
        return 0.0


def analyze_wallet(wallet: str, trades: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not trades:
        return None

    now = time.time()
    timestamps = []
    copyable = 0
    buys = 0
    markets: set[str] = set()

    for t in trades:
        ts = t.get("timestamp")
        try:
            ts_f = float(ts)
            if ts_f > 1e12:
                ts_f /= 1000.0
            timestamps.append(ts_f)
        except (TypeError, ValueError):
            continue

        usd = trade_usd(t)
        try:
            price = float(t.get("price", 0))
        except (TypeError, ValueError):
            price = 0.0

        if usd >= MIN_TRADE_USD and MIN_PRICE <= price <= MAX_PRICE:
            copyable += 1
            if str(t.get("side", "")).upper() == "BUY":
                buys += 1
            market = t.get("title") or t.get("conditionId") or ""
            if market:
                markets.add(str(market))

    if not timestamps:
        return None

    span_days = max((now - min(timestamps)) / 86400.0, 0.5)
    trades_per_day = len(trades) / span_days
    history_truncated = len(trades) >= TRADES_FETCH_LIMIT and span_days < 7

    return {
        "wallet": wallet,
        "total_trades_fetched": len(trades),
        "history_span_days": round(span_days, 1),
        "trades_per_day": round(trades_per_day, 1),
        "copyable_trades": copyable,
        "copyable_buys": buys,
        "distinct_markets": len(markets),
        "history_truncated": history_truncated,
    }


def passes_filters(stats: dict[str, Any]) -> tuple[bool, str]:
    if stats["trades_per_day"] > MAX_TRADES_PER_DAY:
        return False, f"HFT ({stats['trades_per_day']}/day)"
    if stats["copyable_trades"] < MIN_COPYABLE_TRADES:
        return False, f"too few copyable ({stats['copyable_trades']})"
    if stats["distinct_markets"] < 5:
        return False, f"few markets ({stats['distinct_markets']})"
    return True, "ok"


def score(stats: dict[str, Any]) -> float:
    # Reward copyable flow and market diversity; mild penalty for hyperactivity.
    s = stats["copyable_trades"] * 1.0
    s += min(stats["distinct_markets"], 30) * 0.5
    s -= max(stats["trades_per_day"] - 20, 0) * 0.5
    return round(s, 1)


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=100, help="leaderboard size to scan")
    parser.add_argument("--top", type=int, default=10, help="how many candidates to print")
    args = parser.parse_args()

    async with httpx.AsyncClient(headers={"Accept": "application/json"}) as client:
        lb = await fetch_leaderboard(client, args.limit)
        print(f"Leaderboard rows: {len(lb)}", file=sys.stderr)

        candidates = []
        for row in lb:
            try:
                pnl = float(row.get("pnl", 0))
            except (TypeError, ValueError):
                continue
            if MID_TIER_MIN_PNL <= pnl <= MID_TIER_MAX_PNL:
                candidates.append(row)

        print(f"Mid-tier candidates: {len(candidates)}", file=sys.stderr)

        results = []
        for row in candidates:
            wallet = row.get("proxyWallet", "")
            if not wallet:
                continue
            try:
                trades = await fetch_trades(client, wallet)
            except httpx.HTTPStatusError as exc:
                print(f"  {wallet[:10]}… skipped ({exc.response.status_code})", file=sys.stderr)
                await asyncio.sleep(1.0)
                continue

            stats = analyze_wallet(wallet, trades)
            await asyncio.sleep(0.35)  # be nice to the API (429s are real)
            if not stats:
                continue

            ok, reason = passes_filters(stats)
            stats["passes"] = ok
            stats["reject_reason"] = reason
            stats["leaderboard_pnl_30d"] = round(float(row.get("pnl", 0)), 0)
            stats["leaderboard_vol_30d"] = round(float(row.get("vol", 0)), 0)
            stats["username"] = row.get("userName", "")
            stats["score"] = score(stats)
            results.append(stats)
            flag = "PASS" if ok else f"reject: {reason}"
            print(f"  {wallet[:10]}… copyable={stats['copyable_trades']} tpd={stats['trades_per_day']} {flag}", file=sys.stderr)

        passed = sorted([r for r in results if r["passes"]], key=lambda r: -r["score"])

        out_path = Path(__file__).resolve().parent.parent / "data" / "scout_results.json"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps({
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "filters": {
                "min_trade_usd": MIN_TRADE_USD,
                "price_band": [MIN_PRICE, MAX_PRICE],
                "max_trades_per_day": MAX_TRADES_PER_DAY,
                "mid_tier_pnl": [MID_TIER_MIN_PNL, MID_TIER_MAX_PNL],
            },
            "passed": passed,
            "all": results,
        }, indent=2))

        print(f"\nTop {args.top} candidates (saved to {out_path}):\n")
        for i, r in enumerate(passed[: args.top], 1):
            trunc = " [HISTORY TRUNCATED]" if r["history_truncated"] else ""
            print(
                f"{i}. {r['wallet']}\n"
                f"   user={r['username'] or '—'} | 30d PnL=${r['leaderboard_pnl_30d']:,.0f} | vol=${r['leaderboard_vol_30d']:,.0f}\n"
                f"   copyable={r['copyable_trades']} | markets={r['distinct_markets']} | trades/day={r['trades_per_day']} | score={r['score']}{trunc}\n"
            )


if __name__ == "__main__":
    asyncio.run(main())
