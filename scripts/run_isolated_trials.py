#!/usr/bin/env python3
"""Run isolated copy backtests on scout candidates and pick a roster."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dashboard.backtest_engine import CopySettings, run_isolated_backtest, run_roster_backtest

SCOUT_PATH = ROOT / "data" / "scout_results.json"
OUT_PATH = ROOT / "data" / "backtest_results.json"
GENOME_PATH = ROOT / "configs" / "v1.0.0.json"


def load_scout_candidates(limit: int) -> list[dict[str, str]]:
    data = json.loads(SCOUT_PATH.read_text())
    passed = data.get("passed") or []
    return [
        {"wallet": r["wallet"], "username": r.get("username", "")}
        for r in passed[:limit]
    ]


def pick_roster(results: list[dict], roster_size: int) -> list[str]:
    """Prefer positive sim PnL, low trades/day, max exposure capped at $5."""
    eligible = [
        r for r in results
        if not r.get("error")
        and r.get("max_outcome_exposure", 99) <= 5.01
        and r.get("trades_per_day", 999) <= 15
        and r.get("copy_count", 0) >= 10
        and r.get("sim_pnl", -999) > 0
    ]
    eligible.sort(key=lambda r: (-r.get("sim_pnl", 0), -r.get("wins", 0)))
    if len(eligible) >= roster_size:
        return [r["wallet"] for r in eligible[:roster_size]]

    # Fallback: relax PnL filter but keep safety caps
    fallback = [
        r for r in results
        if not r.get("error")
        and r.get("max_outcome_exposure", 99) <= 5.01
        and r.get("trades_per_day", 999) <= 15
        and r.get("copy_count", 0) >= 5
    ]
    fallback.sort(key=lambda r: (-r.get("sim_pnl", 0), -r.get("wins", 0)))
    return [r["wallet"] for r in fallback[:roster_size]]


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidates", type=int, default=15, help="scout rows to trial")
    parser.add_argument("--roster", type=int, default=8, help="wallets for live roster")
    parser.add_argument("--bankroll", type=float, default=50.0, help="for total spend limit")
    args = parser.parse_args()

    settings = CopySettings(
        total_spend_limit_usd=max(args.bankroll - 10.0, 5.0),
    )
    candidates = load_scout_candidates(args.candidates)

    print(f"Trialing {len(candidates)} wallets (total spend cap ${settings.total_spend_limit_usd:.0f})…", file=sys.stderr)

    results = []
    for item in candidates:
        r = await run_isolated_backtest(
            item["wallet"],
            username=item["username"],
            settings=settings,
        )
        results.append(r)
        flag = f"PnL ${r.sim_pnl:+.2f}" if not r.error else f"ERR {r.error}"
        print(
            f"  {item['username'] or item['wallet'][:10]}… copies={r.copy_count} "
            f"W/L={r.wins}/{r.losses} max_out=${r.max_outcome_exposure:.0f} {flag}",
            file=sys.stderr,
        )
        await asyncio.sleep(0.35)

    wallet_rows = [
        {
            "wallet": r.wallet,
            "username": r.username,
            "sim_pnl": r.sim_pnl,
            "copy_count": r.copy_count,
            "wins": r.wins,
            "losses": r.losses,
            "open_count": r.open_count,
            "max_outcome_exposure": r.max_outcome_exposure,
            "trades_per_day": r.trades_per_day,
            "history_days": r.history_days,
            "error": r.error,
        }
        for r in sorted(results, key=lambda x: -x.sim_pnl)
    ]

    roster_wallets = pick_roster(wallet_rows, args.roster)
    roster_items = [w for w in wallet_rows if w["wallet"] in roster_wallets]
    combined = await run_roster_backtest(
        [{"wallet": w, "username": next(x["username"] for x in wallet_rows if x["wallet"] == w)} for w in roster_wallets],
        settings=settings,
    )

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "settings": settings.__dict__,
        "trials": wallet_rows,
        "recommended_roster": roster_wallets,
        "roster_combined": combined,
    }
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(payload, indent=2))

    genome = {
        "version": "v1.0.0",
        "target_wallets": [
            {"address": w["wallet"], "username": w["username"]}
            for w in roster_items
        ],
        "copy_settings": {
            "mode": "fixed",
            "fixed_amount_usd": settings.fixed_amount_usd,
            "max_per_trade_usd": settings.max_per_trade_usd,
            "max_per_yes_no_usd": settings.max_per_yes_no_usd,
            "ignore_below_usd": settings.ignore_below_usd,
            "min_price": settings.min_price,
            "max_price": settings.max_price,
            "total_spend_limit_usd": settings.total_spend_limit_usd,
            "copy_buy_sell": True,
            "auto_redeem": True,
            "reverse_copy": False,
        },
        "notes": "Roster from isolated backtests; max_per_yes_no_usd=5 prevents stacking.",
    }
    GENOME_PATH.write_text(json.dumps(genome, indent=2))

    print(f"\nSaved trials → {OUT_PATH}")
    print(f"Saved genome → {GENOME_PATH}")
    print(f"\nRecommended roster ({len(roster_wallets)} wallets):")
    for i, w in enumerate(roster_items, 1):
        print(
            f"{i}. {w['username'] or w['wallet'][:10]}… {w['wallet']}\n"
            f"   sim PnL ${w['sim_pnl']:+.2f} | copies={w['copy_count']} | W/L={w['wins']}/{w['losses']}"
        )
    print(f"\nCombined roster sim PnL: ${combined['combined_sim_pnl']:+.2f}")


if __name__ == "__main__":
    asyncio.run(main())
