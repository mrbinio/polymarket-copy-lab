#!/usr/bin/env python3
"""Deep screen: category leaderboards, 60/90d history, isolated $5 copy sim.

Not the existing 8-name roster. Writes data/deep_screen.json.
"""

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
OUT = ROOT / "data" / "deep_screen.json"

CATEGORIES = [
    "OVERALL",
    "SPORTS",
    "POLITICS",
    "CRYPTO",
    "FINANCE",
    "ECONOMICS",
    "CULTURE",
    "ESPORTS",
    "TECH",
]
PERIODS = ["MONTH"]
BANNED = {
    "0x84fbaa0a94c4112b871e3ca039d1d2eb5603675e",  # invorser live loss
}

SETTINGS = CopySettings(total_spend_limit_usd=15.0)


def addr(row: dict) -> str:
    return str(row.get("proxyWallet") or row.get("user") or row.get("address") or "").lower()


async def fetch_lb(client: httpx.AsyncClient, category: str, period: str, offset: int = 0) -> list[dict]:
    resp = await client.get(
        f"{DATA_API}/v1/leaderboard",
        params={
            "category": category,
            "timePeriod": period,
            "orderBy": "PNL",
            "limit": 50,
            "offset": offset,
        },
        timeout=30.0,
    )
    resp.raise_for_status()
    data = resp.json()
    return data if isinstance(data, list) else []


def window_sim(trades: list, closed: list, days: int) -> dict[str, Any]:
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
    universe: dict[str, dict[str, Any]] = {}
    method = {
        "pulled_at": datetime.now(timezone.utc).isoformat(),
        "source": "https://data-api.polymarket.com/v1/leaderboard",
        "categories": CATEGORIES,
        "periods": PERIODS,
        "filters": {
            "min_history_days": 60,
            "max_tpd": 15,
            "min_copyable": 6,
            "lb_pnl_month_cap": 250_000,
            "sim_spend_cap_usd": 15,
            "fixed_usd": 5,
            "ignore_below": 20,
        },
        "banned": list(BANNED),
    }

    async with httpx.AsyncClient(headers={"Accept": "application/json", "User-Agent": "Mozilla/5.0 copy-lab-research"}) as client:
        try:
            merlin = await client.get("https://merlin.trade/api/leaderboard", timeout=30.0)
            if merlin.status_code == 200:
                for row in merlin.json() or []:
                    w = addr(row)
                    if not w.startswith("0x") or w in BANNED:
                        continue
                    rec = universe.setdefault(
                        w,
                        {
                            "wallet": w,
                            "username": row.get("userName") or w[:10],
                            "seen_in": [],
                            "lb_pnl": {},
                            "lb_vol": {},
                        },
                    )
                    rec["seen_in"].append("MERLIN:MONTH")
                    rec["lb_pnl"]["MERLIN:MONTH"] = float(row.get("pnl") or 0)
                    rec["lb_vol"]["MERLIN:MONTH"] = float(row.get("vol") or 0)
                print(f"Merlin leaderboard: {len(merlin.json() or [])}", file=sys.stderr)
        except Exception as exc:  # noqa: BLE001
            print(f"Merlin fail: {exc}", file=sys.stderr)

        for cat in CATEGORIES:
            for period in PERIODS:
                try:
                    rows = await fetch_lb(client, cat, period, 0)
                    await asyncio.sleep(0.25)
                    extra = await fetch_lb(client, cat, period, 50)
                    rows = rows + extra
                except Exception as exc:  # noqa: BLE001
                    print(f"LB fail {cat} {period}: {exc}", file=sys.stderr)
                    continue
                print(f"LB {cat} {period}: {len(rows)}", file=sys.stderr)
                for row in rows:
                    w = addr(row)
                    if not w.startswith("0x") or w in BANNED:
                        continue
                    rec = universe.setdefault(
                        w,
                        {
                            "wallet": w,
                            "username": row.get("userName") or row.get("username") or w[:10],
                            "seen_in": [],
                            "lb_pnl": {},
                            "lb_vol": {},
                        },
                    )
                    rec["seen_in"].append(f"{cat}:{period}")
                    rec["lb_pnl"][f"{cat}:{period}"] = float(row.get("pnl") or 0)
                    rec["lb_vol"][f"{cat}:{period}"] = float(row.get("vol") or 0)
                await asyncio.sleep(0.2)

        print(f"unique wallets: {len(universe)}", file=sys.stderr)

        # Cheap history pass
        survivors: list[dict[str, Any]] = []
        for i, (w, rec) in enumerate(universe.items()):
            month_pnls = [v for k, v in rec["lb_pnl"].items() if ":MONTH" in k]
            if month_pnls and max(month_pnls) > 250_000:
                rec["skip"] = "whale_month"
                continue
            try:
                trades = await fetch_trades(client, w, limit=400)
            except Exception as exc:  # noqa: BLE001
                rec["skip"] = f"trades:{exc}"[:80]
                await asyncio.sleep(0.35)
                continue
            ts = [trade_ts(t) for t in trades if trade_ts(t)]
            if not ts:
                rec["skip"] = "no_trades"
                continue
            days = (max(ts) - min(ts)) / 86400
            tpd = len(trades) / max(days, 1)
            rec["history_days_raw"] = round(days, 1)
            rec["tpd_raw"] = round(tpd, 1)
            if days < 60:
                rec["skip"] = f"history_{days:.0f}d"
                continue
            if tpd > 15:
                rec["skip"] = f"hft_{tpd:.1f}_tpd"
                continue
            mix: dict[str, int] = {}
            for t in trades[:200]:
                d = classify_domain(str(t.get("title") or ""))
                mix[d] = mix.get(d, 0) + 1
            rec["domain"] = max(mix, key=mix.get) if mix else "other"
            rec["trades_n"] = len(trades)
            survivors.append(rec)
            if (i + 1) % 20 == 0:
                print(f"history {i+1}/{len(universe)} survivors={len(survivors)}", file=sys.stderr)
            await asyncio.sleep(0.28)

        print(f"history>=60 & tpd<=15: {len(survivors)}", file=sys.stderr)

        # Isolated backtest + 60/90 windows (cap to keep API alive)
        survivors.sort(key=lambda r: -max([v for k, v in r["lb_pnl"].items() if ":MONTH" in k] or [0]))
        to_sim = survivors[:40]
        results = []
        for rec in to_sim:
            w = rec["wallet"]
            bt = await run_isolated_backtest(w, username=rec["username"], settings=SETTINGS)
            await asyncio.sleep(0.35)
            try:
                async with httpx.AsyncClient(headers={"Accept": "application/json"}) as c2:
                    trades = await fetch_trades(c2, w, limit=500)
                    closed = await fetch_closed_positions(c2, w, limit=400)
            except Exception as exc:  # noqa: BLE001
                rec["sim_error"] = str(exc)
                continue
            w60 = window_sim(trades, closed, 60)
            w90 = window_sim(trades, closed, 90)
            row = {
                **rec,
                "sim_pnl": bt.sim_pnl,
                "sim_wins": bt.wins,
                "sim_losses": bt.losses,
                "sim_copies": bt.copy_count,
                "sim_open": bt.open_count,
                "history_days": bt.history_days,
                "tpd": bt.trades_per_day,
                "max_yes_no": bt.max_outcome_exposure,
                "error": bt.error,
                "w60": w60,
                "w90": w90,
            }
            results.append(row)
            flag = "KEEP" if (w60["pnl"] > 0 and w90["pnl"] > 0 and bt.history_days >= 60) else "—"
            print(
                f"{flag} {rec['username'][:18]:18} {rec.get('domain','?'):7} "
                f"d={bt.history_days:.0f} 60d ${w60['pnl']:+.1f} 90d ${w90['pnl']:+.1f} "
                f"all ${bt.sim_pnl:+.1f} tpd={bt.trades_per_day:.1f}",
                file=sys.stderr,
            )
            await asyncio.sleep(0.3)

    keep = [
        r
        for r in results
        if not r.get("error")
        and r.get("history_days", 0) >= 60
        and r.get("tpd", 99) <= 15
        and (r.get("w60") or {}).get("pnl", 0) > 0
        and (r.get("w90") or {}).get("pnl", 0) > 0
        and r.get("sim_copies", 0) >= 6
        and r.get("max_yes_no", 99) <= 5.01
    ]
    keep.sort(key=lambda r: (-r["w90"]["pnl"], -r["w60"]["pnl"]))

    payload = {
        "method": method,
        "universe_size": len(universe),
        "history_pass": len(survivors),
        "simulated": len(results),
        "passed_60_90": keep,
        "all_simulated": results,
        "skipped_counts": {},
    }
    skips: dict[str, int] = {}
    for rec in universe.values():
        k = rec.get("skip") or "ok_or_sim"
        if k.startswith("history_"):
            k = "history_<60d"
        if k.startswith("hft_"):
            k = "hft_tpd"
        if k.startswith("trades:"):
            k = "trade_fetch_error"
        skips[k] = skips.get(k, 0) + 1
    payload["skipped_counts"] = skips

    OUT.write_text(json.dumps(payload, indent=2))
    print(f"wrote {OUT} passed={len(keep)}", file=sys.stderr)


if __name__ == "__main__":
    asyncio.run(main())
