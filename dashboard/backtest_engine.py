"""Isolated copy-trading backtest under PolyCop-style filters."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

import httpx

from dashboard.polymarket_api import DATA_API


@dataclass
class CopySettings:
    fixed_amount_usd: float = 5.0
    ignore_below_usd: float = 20.0
    min_price: float = 0.15
    max_price: float = 0.85
    max_per_trade_usd: float = 5.0
    max_per_yes_no_usd: float = 5.0
    total_spend_limit_usd: float = 40.0


@dataclass
class SimPosition:
    condition_id: str
    outcome: str
    outcome_index: int
    asset: str
    title: str
    cost_usd: float
    shares: float
    entry_price: float
    entry_ts: float


@dataclass
class BacktestResult:
    wallet: str
    username: str
    copy_count: int
    wins: int
    losses: int
    open_count: int
    sim_pnl: float
    total_spent: float
    max_outcome_exposure: float
    history_days: float
    trades_per_day: float
    positions: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None


def _trade_usd(trade: dict[str, Any]) -> float:
    try:
        usdc = float(trade.get("usdcSize") or 0)
        if usdc > 0:
            return usdc
        return float(trade.get("size", 0)) * float(trade.get("price", 0))
    except (TypeError, ValueError):
        return 0.0


def _ts(trade: dict[str, Any]) -> float:
    try:
        val = float(trade.get("timestamp", 0))
        return val / 1000.0 if val > 1e12 else val
    except (TypeError, ValueError):
        return 0.0


def _outcome_key(trade: dict[str, Any]) -> str:
    cid = str(trade.get("conditionId") or "")
    idx = trade.get("outcomeIndex", "")
    return f"{cid}:{idx}"


async def fetch_trades(client: httpx.AsyncClient, wallet: str, limit: int = 500) -> list[dict[str, Any]]:
    resp = await client.get(
        f"{DATA_API}/trades",
        params={"user": wallet, "limit": limit},
        timeout=45.0,
    )
    resp.raise_for_status()
    data = resp.json()
    return data if isinstance(data, list) else []


async def fetch_activity(client: httpx.AsyncClient, wallet: str, limit: int = 500) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    offset = 0
    page = min(limit, 500)
    while len(rows) < limit:
        resp = await client.get(
            f"{DATA_API}/activity",
            params={"user": wallet, "limit": page, "offset": offset},
            timeout=45.0,
        )
        resp.raise_for_status()
        batch = resp.json()
        if not isinstance(batch, list) or not batch:
            break
        rows.extend(batch)
        if len(batch) < page:
            break
        offset += page
        await asyncio.sleep(0.15)
    return rows[:limit]


def _copyable_buy(trade: dict[str, Any], settings: CopySettings) -> bool:
    if str(trade.get("side", "")).upper() != "BUY":
        return False
    usd = _trade_usd(trade)
    if usd < settings.ignore_below_usd:
        return False
    try:
        price = float(trade.get("price", 0))
    except (TypeError, ValueError):
        return False
    return settings.min_price <= price <= settings.max_price


def _closed_ts(row: dict[str, Any]) -> float:
    try:
        val = float(row.get("timestamp", 0))
        return val / 1000.0 if val > 1e12 else val
    except (TypeError, ValueError):
        return 0.0


def simulate_copies(
    trades: list[dict[str, Any]],
    settings: CopySettings,
    closed: list[dict[str, Any]] | None = None,
) -> tuple[list[SimPosition], float]:
    exposure: dict[str, float] = {}
    positions: list[SimPosition] = []
    open_positions: list[SimPosition] = []
    cmap = _closed_map(closed or [])

    buys = sorted(
        [t for t in trades if _copyable_buy(t, settings)],
        key=_ts,
    )

    for trade in buys:
        ts = _ts(trade)
        still_open: list[SimPosition] = []
        open_cost = 0.0
        open_exposure: dict[str, float] = {}

        for pos in open_positions:
            key = f"{pos.condition_id}:{pos.outcome}"
            row = cmap.get(key)
            settle_ts = _closed_ts(row) if row else 0.0
            if row and settle_ts and settle_ts <= ts:
                continue
            still_open.append(pos)
            open_cost += pos.cost_usd
            okey = f"{pos.condition_id}:{pos.outcome_index}"
            open_exposure[okey] = open_exposure.get(okey, 0.0) + pos.cost_usd

        open_positions = still_open

        key = _outcome_key(trade)
        already = open_exposure.get(key, exposure.get(key, 0.0))
        if already >= settings.max_per_yes_no_usd:
            continue
        if open_cost + settings.fixed_amount_usd > settings.total_spend_limit_usd:
            continue

        try:
            price = float(trade.get("price", 0))
        except (TypeError, ValueError):
            continue
        if price <= 0:
            continue

        amount = min(settings.fixed_amount_usd, settings.max_per_trade_usd)
        room = settings.max_per_yes_no_usd - already
        amount = min(amount, room)
        if amount <= 0:
            continue

        shares = amount / price
        pos = SimPosition(
            condition_id=str(trade.get("conditionId") or ""),
            outcome=str(trade.get("outcome") or "—"),
            outcome_index=int(trade.get("outcomeIndex") or 0),
            asset=str(trade.get("asset") or ""),
            title=str(trade.get("title") or trade.get("slug") or "Unknown"),
            cost_usd=round(amount, 2),
            shares=shares,
            entry_price=price,
            entry_ts=ts,
        )
        positions.append(pos)
        open_positions.append(pos)
        exposure[key] = max(exposure.get(key, 0.0), already + amount)

    max_outcome = max(exposure.values(), default=0.0)
    return positions, max_outcome


async def fetch_closed_positions(
    client: httpx.AsyncClient,
    wallet: str,
    limit: int = 500,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    offset = 0
    page = min(limit, 100)
    while len(rows) < limit:
        resp = await client.get(
            f"{DATA_API}/closed-positions",
            params={"user": wallet, "limit": page, "offset": offset},
            timeout=45.0,
        )
        resp.raise_for_status()
        batch = resp.json()
        if not isinstance(batch, list) or not batch:
            break
        rows.extend(batch)
        if len(batch) < page:
            break
        offset += page
        await asyncio.sleep(0.15)
    return rows[:limit]


def _closed_map(closed: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for row in closed:
        key = f"{row.get('conditionId')}:{row.get('outcome')}"
        out[key] = row
    return out


def settle_positions(
    positions: list[SimPosition],
    closed: list[dict[str, Any]],
) -> tuple[float, int, int, int, list[dict[str, Any]]]:
    total_pnl = 0.0
    wins = losses = open_count = 0
    rows: list[dict[str, Any]] = []
    cmap = _closed_map(closed)

    for pos in positions:
        key = f"{pos.condition_id}:{pos.outcome}"
        row = cmap.get(key)
        if not row:
            open_count += 1
            rows.append(
                {
                    "market": pos.title,
                    "outcome": pos.outcome,
                    "cost_usd": pos.cost_usd,
                    "pnl": None,
                    "status": "open",
                }
            )
            continue

        try:
            cur = float(row.get("curPrice", 0))
        except (TypeError, ValueError):
            cur = 0.0

        if cur >= 0.99:
            pnl = round(pos.shares * 1.0 - pos.cost_usd, 2)
            wins += 1
            status = "win"
        else:
            pnl = round(-pos.cost_usd, 2)
            losses += 1
            status = "loss"

        total_pnl += pnl
        rows.append(
            {
                "market": pos.title,
                "outcome": pos.outcome,
                "cost_usd": pos.cost_usd,
                "pnl": pnl,
                "status": status,
            }
        )

    return round(total_pnl, 2), wins, losses, open_count, rows


async def run_isolated_backtest(
    wallet: str,
    *,
    username: str = "",
    settings: CopySettings | None = None,
    trade_limit: int = 500,
    activity_limit: int = 500,
) -> BacktestResult:
    settings = settings or CopySettings()

    async with httpx.AsyncClient(headers={"Accept": "application/json"}) as client:
        try:
            trades = await fetch_trades(client, wallet, limit=trade_limit)
            closed = await fetch_closed_positions(client, wallet, limit=activity_limit)
        except httpx.HTTPError as exc:
            return BacktestResult(
                wallet=wallet,
                username=username,
                copy_count=0,
                wins=0,
                losses=0,
                open_count=0,
                sim_pnl=0.0,
                total_spent=0.0,
                max_outcome_exposure=0.0,
                history_days=0.0,
                trades_per_day=0.0,
                error=str(exc),
            )

    if not trades:
        return BacktestResult(
            wallet=wallet,
            username=username,
            copy_count=0,
            wins=0,
            losses=0,
            open_count=0,
            sim_pnl=0.0,
            total_spent=0.0,
            max_outcome_exposure=0.0,
            history_days=0.0,
            trades_per_day=0.0,
            error="no trades",
        )

    timestamps = [_ts(t) for t in trades if _ts(t) > 0]
    span_days = max((max(timestamps) - min(timestamps)) / 86400.0, 0.5) if timestamps else 0.5
    trades_per_day = len(trades) / span_days

    positions, max_outcome = simulate_copies(trades, settings, closed)
    sim_pnl, wins, losses, open_count, pos_rows = settle_positions(positions, closed)
    total_spent = round(sum(p.cost_usd for p in positions), 2)

    return BacktestResult(
        wallet=wallet,
        username=username,
        copy_count=len(positions),
        wins=wins,
        losses=losses,
        open_count=open_count,
        sim_pnl=sim_pnl,
        total_spent=total_spent,
        max_outcome_exposure=round(max_outcome, 2),
        history_days=round(span_days, 1),
        trades_per_day=round(trades_per_day, 1),
        positions=pos_rows,
    )


async def run_roster_backtest(
    wallets: list[dict[str, str]],
    settings: CopySettings | None = None,
) -> dict[str, Any]:
    settings = settings or CopySettings()
    results: list[BacktestResult] = []

    for item in wallets:
        wallet = item.get("wallet", "")
        if not wallet:
            continue
        result = await run_isolated_backtest(
            wallet,
            username=item.get("username", ""),
            settings=settings,
        )
        results.append(result)
        await asyncio.sleep(0.35)

    settled = [r for r in results if not r.error]
    combined_pnl = round(sum(r.sim_pnl for r in settled), 2)
    combined_copies = sum(r.copy_count for r in settled)

    return {
        "settings": settings.__dict__,
        "combined_sim_pnl": combined_pnl,
        "combined_copy_count": combined_copies,
        "wallets": [
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
                "error": r.error,
            }
            for r in sorted(results, key=lambda x: -x.sim_pnl)
        ],
    }
