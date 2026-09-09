"""Polymarket Data API helpers."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

import httpx

DATA_API = "https://data-api.polymarket.com"
GAMMA_API = "https://gamma-api.polymarket.com"
POLYGON_RPCS = (
    "https://1rpc.io/matic",
    "https://polygon-rpc.com",
    "https://rpc.ankr.com/polygon",
)

# USDC contracts on Polygon (Polymarket collateral is USDC.e)
USDC_E_CONTRACT = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"
USDC_NATIVE_CONTRACT = "0x3c499c542cEF5E3811e1192ce70d8cC03d5c3359"

MAX_ACTIVITY_OFFSET = 4000


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
        while offset <= MAX_ACTIVITY_OFFSET:
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


async def fetch_open_positions(wallet: str) -> list[dict[str, Any]]:
    """Fetch current open positions from the Data API."""
    rows: list[dict[str, Any]] = []
    offset = 0
    limit = 100

    async with httpx.AsyncClient(headers={"Accept": "application/json"}) as client:
        while offset <= MAX_ACTIVITY_OFFSET:
            data = await fetch_json(
                client,
                "/positions",
                {"user": wallet, "limit": limit, "offset": offset},
            )
            batch = data if isinstance(data, list) else []
            if not batch:
                break
            rows.extend(batch)
            if len(batch) < limit:
                break
            offset += limit
            await asyncio.sleep(0.15)

    return rows


async def fetch_portfolio_value(wallet: str) -> float | None:
    """Sum of open position values from /value endpoint."""
    async with httpx.AsyncClient(headers={"Accept": "application/json"}) as client:
        try:
            data = await fetch_json(client, "/value", {"user": wallet})
        except Exception:  # noqa: BLE001
            return None
    if isinstance(data, list) and data:
        try:
            return round(float(data[0].get("value", 0)), 2)
        except (TypeError, ValueError):
            return None
    return None


def classify_position(row: dict[str, Any]) -> str:
    if row.get("redeemable"):
        return "redeemable"
    try:
        price = float(row.get("curPrice") or row.get("currentPrice") or 0)
    except (TypeError, ValueError):
        price = 0.0
    try:
        value = float(row.get("currentValue") or 0)
    except (TypeError, ValueError):
        value = 0.0
    if price < 0.02 or value < 0.05:
        return "dead"
    return "open"


async def enrich_positions_opened_at(
    wallet: str,
    positions: list[dict[str, Any]],
    *,
    since_ts: float,
) -> None:
    """Attach opened_at (unix) from latest matching BUY in activity."""
    if not positions:
        return

    rows = await fetch_all_activity_since(wallet, since_ts)
    latest_buy: dict[str, float] = {}
    for row in rows:
        if row.get("type") != "TRADE" or row.get("side") != "BUY":
            continue
        title = str(row.get("title") or row.get("question") or "")
        ts = _row_ts(row)
        if not title or not ts:
            continue
        if title not in latest_buy or ts > latest_buy[title]:
            latest_buy[title] = ts

    for pos in positions:
        ts = latest_buy.get(pos["market"])
        if ts:
            pos["opened_at"] = ts


def _parse_iso_ts(value: str | None) -> float | None:
    if not value:
        return None
    raw = str(value).strip()
    if not raw:
        return None
    if len(raw) == 10 and raw[4] == "-":
        raw = raw + "T23:59:59Z"
    try:
        if raw.endswith("Z"):
            raw = raw.replace("Z", "+00:00")
        return datetime.fromisoformat(raw).timestamp()
    except ValueError:
        return None


async def fetch_event_end_times(slugs: list[str]) -> dict[str, float]:
    """Map event slug → expected close unix time (gamma API)."""
    unique = [s for s in dict.fromkeys(slugs) if s]
    if not unique:
        return {}

    async def one(slug: str) -> tuple[str, float | None]:
        async with httpx.AsyncClient(headers={"Accept": "application/json"}) as client:
            try:
                resp = await client.get(
                    f"{GAMMA_API}/events",
                    params={"slug": slug},
                    timeout=15.0,
                )
                resp.raise_for_status()
                data = resp.json()
            except Exception:  # noqa: BLE001
                return slug, None
        if not isinstance(data, list) or not data:
            return slug, None
        ts = _parse_iso_ts(str(data[0].get("endDate") or ""))
        return slug, ts

    results = await asyncio.gather(*(one(s) for s in unique))
    return {slug: ts for slug, ts in results if ts is not None}


async def enrich_positions_expected_close(positions: list[dict[str, Any]]) -> None:
    slugs = [str(p.get("event_slug") or "") for p in positions]
    ends = await fetch_event_end_times(slugs)
    for pos in positions:
        slug = str(pos.get("event_slug") or "")
        ts = ends.get(slug)
        if ts is None:
            ts = _parse_iso_ts(str(pos.get("end_date") or ""))
        if ts:
            pos["expected_close_at"] = ts


def open_positions_meta(positions: list[dict[str, Any]]) -> dict[str, Any]:
    opened = [p for p in positions if p.get("opened_at")]
    oldest = min((p["opened_at"] for p in opened), default=None)
    closes = [p["expected_close_at"] for p in positions if p.get("expected_close_at")]
    nearest_close = min(closes) if closes else None
    return {
        "count": len(positions),
        "with_open_time": len(opened),
        "oldest_opened_at": oldest,
        "nearest_expected_close_at": nearest_close,
    }


def open_position_summary(row: dict[str, Any]) -> dict[str, Any]:
    def _f(*keys: str) -> float:
        for k in keys:
            v = row.get(k)
            if v is not None:
                try:
                    return float(v)
                except (TypeError, ValueError):
                    continue
        return 0.0

    status = classify_position(row)
    return {
        "market": str(row.get("title") or row.get("question") or "Unknown market"),
        "outcome": str(row.get("outcome") or row.get("outcomeName") or "—"),
        "invested": round(_f("initialValue"), 2),
        "value": round(_f("currentValue"), 2),
        "pnl": round(_f("cashPnl"), 2),
        "cur_price": _f("curPrice", "currentPrice"),
        "redeemable": bool(row.get("redeemable", False)),
        "status": status,
        "end_date": str(row.get("endDate") or ""),
        "event_slug": str(row.get("eventSlug") or ""),
    }


async def fetch_activity_page(
    client: httpx.AsyncClient,
    wallet: str,
    *,
    limit: int = 100,
    offset: int = 0,
) -> list[dict[str, Any]]:
    data = await fetch_json(
        client,
        "/activity",
        {"user": wallet, "limit": limit, "offset": offset},
    )
    return data if isinstance(data, list) else []


async def fetch_all_activity_since(
    wallet: str,
    since_ts: float,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    offset = 0
    limit = 100

    async with httpx.AsyncClient(headers={"Accept": "application/json"}) as client:
        while offset <= MAX_ACTIVITY_OFFSET:
            batch = await fetch_activity_page(client, wallet, limit=limit, offset=offset)
            if not batch:
                break

            stop = False
            for row in batch:
                ts = _row_ts(row)
                if ts and ts >= since_ts:
                    rows.append(row)
                elif ts and ts < since_ts:
                    stop = True

            if stop or len(batch) < limit:
                break
            offset += limit
            await asyncio.sleep(0.12)

    rows.sort(key=_row_ts, reverse=True)
    return rows


def activity_event_summary(row: dict[str, Any]) -> dict[str, Any]:
    """Cash impact of one activity row (REDEEM / TRADE)."""
    usdc = float(row.get("usdcSize") or 0)
    kind = str(row.get("type") or "")
    side = str(row.get("side") or "")
    title = str(row.get("title") or row.get("question") or "Unknown market")
    ts = _row_ts(row)

    cash_delta = 0.0
    if kind == "REDEEM":
        cash_delta = usdc
    elif kind == "TRADE":
        if side == "BUY":
            cash_delta = -usdc
        elif side == "SELL":
            cash_delta = usdc

    return {
        "time": datetime.fromtimestamp(ts, tz=timezone.utc).isoformat() if ts else "",
        "raw_ts": ts,
        "type": kind,
        "side": side,
        "usdc": round(usdc, 2),
        "cash_delta": round(cash_delta, 2),
        "market": title,
        "outcome": str(row.get("outcome") or "—"),
        "status": "settled" if kind == "REDEEM" else kind.lower(),
    }


def summarize_activity_cashflow(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Real cashflow from activity API (includes losing redeems at $0)."""
    buys = redeems = sells = 0.0
    events: list[dict[str, Any]] = []

    for row in rows:
        ev = activity_event_summary(row)
        events.append(ev)
        if ev["type"] == "TRADE" and ev["side"] == "BUY":
            buys += ev["usdc"]
        elif ev["type"] == "TRADE" and ev["side"] == "SELL":
            sells += ev["usdc"]
        elif ev["type"] == "REDEEM":
            redeems += ev["usdc"]

    net = round(redeems + sells - buys, 2)
    losses = [e for e in events if e["type"] == "REDEEM" and e["usdc"] <= 0.01]
    wins = [e for e in events if e["type"] == "REDEEM" and e["usdc"] > 0.01]

    return {
        "buys_usd": round(buys, 2),
        "redeems_usd": round(redeems, 2),
        "sells_usd": round(sells, 2),
        "net_cashflow_usd": net,
        "redeem_wins": len(wins),
        "redeem_losses": len(losses),
        "events": events,
    }


async def compute_wallet_state(
    wallet: str,
    *,
    cash_anchor_usd: float | None,
    cash_anchor_at: float,
    sync_positions_usd: float | None = None,
    polycop_positions_usd: float | None = None,
    sync_total_usd: float | None = None,
    eval_since_ts: float | None,
    eval_baseline_usd: float | None,
    history_since_ts: float | None = None,
) -> dict[str, Any]:
    """
    Wallet truth model (PolyCop-aligned):
      positions = live sum from Polymarket /positions API (≈ PolyCop Positions Value)
      total = polycop_total_at_sync + (positions_now - positions_at_sync) + cashflow since sync
      available = total - positions
    """
    raw_positions = await fetch_open_positions(wallet)
    open_positions = [open_position_summary(p) for p in raw_positions]
    open_positions.sort(key=lambda p: p["pnl"])

    await enrich_positions_opened_at(
        wallet,
        open_positions,
        since_ts=datetime.now(timezone.utc).timestamp() - 30 * 86400,
    )
    await enrich_positions_expected_close(open_positions)

    positions_value = round(sum(p["value"] for p in open_positions), 2)
    open_pnl = round(sum(p["pnl"] for p in open_positions), 2)
    positions_invested = round(sum(p["invested"] for p in open_positions), 2)
    api_value = await fetch_portfolio_value(wallet)

    cash_flow_since: list[dict[str, Any]] = []
    cash_net = 0.0
    available_cash: float | None = None
    needs_sync = cash_anchor_usd is None

    if cash_anchor_usd is not None and cash_anchor_at:
        cash_flow_since = await fetch_all_activity_since(wallet, cash_anchor_at)
        cash_net = summarize_activity_cashflow(cash_flow_since)["net_cashflow_usd"]
        available_cash = round(float(cash_anchor_usd) + cash_net, 2)

    positions_display = positions_value

    account_total: float | None = None
    if sync_total_usd is not None and sync_positions_usd is not None:
        pos_delta = round(positions_value - float(sync_positions_usd), 2)
        account_total = round(float(sync_total_usd) + pos_delta + cash_net, 2)
        available_cash = round(account_total - positions_display, 2)
    elif available_cash is not None:
        account_total = round(available_cash + positions_display, 2)

    session_flow: dict[str, Any] = {"events": [], "buys_usd": 0, "redeems_usd": 0, "net_cashflow_usd": 0}
    settled_events: list[dict[str, Any]] = []
    history_flow: dict[str, Any] = {"events": [], "buys_usd": 0, "redeems_usd": 0, "net_cashflow_usd": 0}
    history_since = history_since_ts or eval_since_ts or (cash_anchor_at if cash_anchor_at else None)

    if eval_since_ts:
        session_rows = await fetch_all_activity_since(wallet, eval_since_ts)
        session_flow = summarize_activity_cashflow(session_rows)
        settled_events = [e for e in session_flow["events"] if e["type"] == "REDEEM"]

    if history_since:
        history_rows = await fetch_all_activity_since(wallet, float(history_since))
        history_flow = summarize_activity_cashflow(history_rows)
        if not settled_events:
            settled_events = [e for e in history_flow["events"] if e["type"] == "REDEEM"]

    session_pnl: float | None = None
    if account_total is not None and eval_baseline_usd is not None:
        session_pnl = round(account_total - float(eval_baseline_usd), 2)

    position_counts = {
        "open": sum(1 for p in open_positions if p["status"] == "open"),
        "dead": sum(1 for p in open_positions if p["status"] == "dead"),
        "redeemable": sum(1 for p in open_positions if p["status"] == "redeemable"),
    }

    return {
        "account_total_usd": account_total,
        "available_cash_usd": available_cash,
        "positions_value_usd": positions_display,
        "positions_api_usd": positions_value,
        "positions_invested_usd": positions_invested,
        "open_pnl_usd": open_pnl,
        "positions_api_value_usd": api_value,
        "open_positions": open_positions,
        "open_positions_meta": open_positions_meta(open_positions),
        "position_counts": position_counts,
        "cashflow_since_sync": summarize_activity_cashflow(cash_flow_since) if cash_flow_since else None,
        "session_cashflow": session_flow,
        "history_cashflow": history_flow,
        "settled_events": settled_events,
        "session_pnl_usd": session_pnl,
        "needs_sync": needs_sync,
        "polycop_total_at_sync": sync_total_usd,
    }


async def fetch_usdc_cash(wallet: str) -> float | None:
    """On-chain USDC + USDC.e balance. Often 0 for PolyCop proxy wallets."""
    padded = wallet.lower().replace("0x", "").rjust(64, "0")
    call_data = "0x70a08231" + padded

    total = 0.0
    got_any = False
    async with httpx.AsyncClient() as client:
        for rpc in POLYGON_RPCS:
            for contract in (USDC_E_CONTRACT, USDC_NATIVE_CONTRACT):
                payload = {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "eth_call",
                    "params": [{"to": contract, "data": call_data}, "latest"],
                }
                try:
                    resp = await client.post(rpc, json=payload, timeout=10.0)
                    resp.raise_for_status()
                    result = resp.json().get("result")
                    if result:
                        total += int(result, 16) / 1e6
                        got_any = True
                except Exception:  # noqa: BLE001
                    continue
            if got_any:
                break

    return round(total, 2) if got_any else None


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
        "status": "closed",
    }


# Backward compat for scripts still importing compute_live_account
async def compute_live_account(
    wallet: str,
    *,
    anchor_total_usd: float,
    since_ts: float,
) -> dict[str, Any]:
    state = await compute_wallet_state(
        wallet,
        cash_anchor_usd=anchor_total_usd,
        cash_anchor_at=since_ts,
        eval_since_ts=since_ts,
        eval_baseline_usd=anchor_total_usd,
    )
    flow = state["session_cashflow"] or {"events": [], "buys_usd": 0, "redeems_usd": 0, "net_cashflow_usd": 0}
    return {
        "account_total_usd": state["account_total_usd"] or 0.0,
        "available_cash_usd": state["available_cash_usd"] or 0.0,
        "positions_value_usd": state["positions_value_usd"],
        "session_pnl_usd": state["session_pnl_usd"] or 0.0,
        "cashflow": flow,
        "open_positions": state["open_positions"],
        "open_pnl_usd": state["open_pnl_usd"],
    }
