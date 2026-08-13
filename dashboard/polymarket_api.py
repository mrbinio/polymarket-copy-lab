"""Polymarket Data API helpers."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

import httpx

DATA_API = "https://data-api.polymarket.com"


async def fetch_json(
    client: httpx.AsyncClient,
    path: str,
    params: dict[str, Any] | None = None,
) -> list[dict[str, Any]] | dict[str, Any]:
    resp = await client.get(f"{DATA_API}{path}", params=params or {}, timeout=30.0)
    resp.raise_for_status()
    return resp.json()


async def fetch_closed_positions(
    client: httpx.AsyncClient,
    wallet: str,
    *,
    limit: int = 100,
    offset: int = 0,
) -> list[dict[str, Any]]:
    data = await fetch_json(
        client,
        "/closed-positions",
        {"user": wallet, "limit": limit, "offset": offset},
    )
    return data if isinstance(data, list) else []


async def fetch_all_closed_since(
    wallet: str,
    since_ts: float,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    offset = 0
    limit = 100

    async with httpx.AsyncClient(headers={"Accept": "application/json"}) as client:
        while True:
            batch = await fetch_closed_positions(client, wallet, limit=limit, offset=offset)
            if not batch:
                break

            for row in batch:
                ts = _row_ts(row)
                if ts and ts >= since_ts:
                    rows.append(row)

            if len(batch) < limit:
                break
            offset += limit
            await asyncio.sleep(0.2)

    rows.sort(key=_row_ts, reverse=True)
    return rows


def _row_ts(row: dict[str, Any]) -> float:
    for key in ("timestamp", "closedAt", "endDate", "updatedAt"):
        val = row.get(key)
        if val is None:
            continue
        if isinstance(val, (int, float)):
            return float(val if val > 1e12 else val)
        if isinstance(val, str):
            try:
                dt = datetime.fromisoformat(val.replace("Z", "+00:00"))
                return dt.timestamp()
            except ValueError:
                continue
    return 0.0


def settlement_from_closed(row: dict[str, Any]) -> dict[str, Any]:
    pnl = row.get("realizedPnl") or row.get("pnl") or row.get("cashPnl") or 0
    try:
        pnl_f = float(pnl)
    except (TypeError, ValueError):
        pnl_f = 0.0

    title = row.get("title") or row.get("marketTitle") or row.get("question") or "Unknown market"
    outcome = row.get("outcome") or row.get("outcomeName") or row.get("side") or "—"
    ts = _row_ts(row)

    return {
        "time": datetime.fromtimestamp(ts, tz=timezone.utc).isoformat() if ts else "",
        "outcome": str(outcome),
        "pnl": round(pnl_f, 2),
        "market": str(title),
        "raw_ts": ts,
    }
