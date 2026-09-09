"""Live monitoring dashboard — PolyCop-aligned wallet state."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from dashboard.polymarket_api import compute_wallet_state, fetch_all_closed_since, settlement_from_closed

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "configs" / "monitor.json"
SESSION_PATH = ROOT / "data" / "session_state.json"
DEFAULT_GENOME = ROOT / "configs" / "v1.0.0.json"

app = FastAPI(title="Polymarket Copy Lab")
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
app.mount("/static", StaticFiles(directory=str(Path(__file__).parent / "static")), name="static")


@app.middleware("http")
async def access_key_guard(request: Request, call_next):
    key = load_monitor().get("dashboard_key")
    if not key:
        return await call_next(request)

    supplied = request.query_params.get("key") or request.cookies.get("dash_key")
    if supplied != key:
        return JSONResponse({"error": "unauthorized — open /?key=YOUR_KEY"}, status_code=403)

    response = await call_next(request)
    if request.query_params.get("key") == key:
        response.set_cookie("dash_key", key, max_age=365 * 24 * 3600, httponly=True)
    return response


def load_monitor() -> dict:
    if CONFIG_PATH.exists():
        return json.loads(CONFIG_PATH.read_text())
    example = ROOT / "configs" / "monitor.example.json"
    return json.loads(example.read_text())


def load_genome() -> dict:
    monitor = load_monitor()
    name = monitor.get("active_config", "v1.0.0")
    path = ROOT / "configs" / f"{name}.json"
    if not path.exists():
        path = DEFAULT_GENOME
    if path.exists():
        return json.loads(path.read_text())
    return {"version": name, "target_wallets": [], "copy_settings": {}}


def load_session() -> dict:
    if SESSION_PATH.exists():
        return json.loads(SESSION_PATH.read_text())
    return {
        "active": False,
        "started_at": None,
        "eval_baseline_usd": None,
        "polycop_available_usd": None,
        "polycop_sync_at": None,
        "polycop_total_usd": None,
    }


def save_session(state: dict) -> None:
    SESSION_PATH.parent.mkdir(parents=True, exist_ok=True)
    SESSION_PATH.write_text(json.dumps(state, indent=2))


def _localize_events(events: list[dict], tz_name: str) -> None:
    tz = ZoneInfo(tz_name)
    for ev in events:
        if ev.get("raw_ts"):
            ev["time_local"] = (
                datetime.fromtimestamp(ev["raw_ts"], tz=timezone.utc)
                .astimezone(tz)
                .strftime("%H:%M %d.%m")
            )


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    monitor = load_monitor()
    session = load_session()
    genome = load_genome()
    return templates.TemplateResponse(
        "index.html",
        {"request": request, "monitor": monitor, "session": session, "genome": genome},
    )


@app.get("/api/status")
async def status():
    monitor = load_monitor()
    session = load_session()
    genome = load_genome()
    wallet = monitor.get("poly_wallet", "")

    if not wallet or wallet.startswith("0xYOUR"):
        return {
            "wallet_configured": False,
            "session_active": session.get("active", False),
            "config_version": genome.get("version", "v1.0.0"),
            "error": "poly_wallet not configured",
        }

    eval_since = float(session["started_at"]) if session.get("started_at") else None
    eval_baseline = session.get("eval_baseline_usd")

    try:
        state = await compute_wallet_state(
            wallet,
            cash_anchor_usd=session.get("polycop_available_usd"),
            cash_anchor_at=float(session.get("polycop_sync_at") or 0),
            eval_since_ts=eval_since,
            eval_baseline_usd=float(eval_baseline) if eval_baseline is not None else None,
        )
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"error": str(exc), "session": session}, status_code=502)

    tz_name = monitor.get("display_timezone", "Europe/Warsaw")
    session_flow = state.get("session_cashflow") or {"events": []}
    sync_flow = state.get("cashflow_since_sync") or {"events": []}
    _localize_events(session_flow.get("events", []), tz_name)
    _localize_events(sync_flow.get("events", []), tz_name)
    _localize_events(state.get("settled_events", []), tz_name)

    closed = []
    if eval_since:
        closed = await fetch_all_closed_since(wallet, eval_since)
    settlements = [settlement_from_closed(r) for r in closed]
    _localize_events(settlements, tz_name)

    sync_total = session.get("polycop_total_usd")
    account_total = state.get("account_total_usd")
    drift: float | None = None
    if sync_total is not None and account_total is not None:
        drift = round(account_total - float(sync_total), 2)

    session_pnl = state.get("session_pnl_usd")
    stop_loss = float(monitor.get("stop_loss_usd") or 10)
    stop_breached = session_pnl is not None and session_pnl <= -stop_loss
    available = state.get("available_cash_usd")
    positions_value = state.get("positions_value_usd")
    open_pnl = state.get("open_pnl_usd")
    settled_net = round(float(session_flow.get("net_cashflow_usd") or 0), 2)

    return {
        "session_active": session.get("active", False),
        "started_at": session.get("started_at"),
        "eval_baseline_usd": session.get("eval_baseline_usd"),
        "poll_interval_seconds": monitor.get("poll_interval_seconds", 15),
        "config_version": genome.get("version", "v1.0.0"),
        "wallet_count": len(genome.get("target_wallets", [])),
        "needs_sync": state.get("needs_sync", False),
        "polycop_sync_at": session.get("polycop_sync_at"),
        "polycop_total_usd": sync_total,
        "polycop_available_usd": session.get("polycop_available_usd"),
        "account_total_usd": account_total,
        "available_cash_usd": available,
        "positions_value_usd": positions_value,
        "positions_invested_usd": state.get("positions_invested_usd"),
        "open_pnl_usd": open_pnl,
        "positions_api_value_usd": state.get("positions_api_value_usd"),
        "session_pnl_usd": session_pnl,
        "stop_loss_usd": stop_loss,
        "stop_breached": stop_breached,
        # aliases for the live HTML (old field names)
        "session_total": session_pnl if session_pnl is not None else 0,
        "available_cash": available,
        "est_total": account_total,
        "positions_value": positions_value,
        "open_pnl": open_pnl if open_pnl is not None else 0,
        "in_window_net": settled_net,
        "resolved_count": len(settlements),
        "cash_source": "override" if session.get("polycop_available_usd") is not None else None,
        "position_counts": state.get("position_counts"),
        "open_positions": state.get("open_positions"),
        "settled_events": state.get("settled_events", [])[:30],
        "closed_positions": settlements[:30],
        "settlements": settlements[:30],
        "activity_events": session_flow.get("events", [])[:40],
        "cashflow_buys_usd": session_flow.get("buys_usd", 0),
        "cashflow_redeems_usd": session_flow.get("redeems_usd", 0),
        "cashflow_net_usd": session_flow.get("net_cashflow_usd", 0),
        "redeem_wins": session_flow.get("redeem_wins", 0),
        "redeem_losses": session_flow.get("redeem_losses", 0),
        "sync_drift_usd": drift,
        "wallet_configured": True,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }


@app.post("/api/eval/start")
async def eval_start():
    session = load_session()
    wallet = load_monitor().get("poly_wallet", "")

    if session.get("polycop_available_usd") is None:
        return JSONResponse(
            {"ok": False, "error": "Najpierw Sync — wpisz Available Balance z PolyCop Wallet"},
            status_code=400,
        )

    if session.get("active"):
        return {"ok": True, "already_active": True, "session": session}

    state = await compute_wallet_state(
        wallet,
        cash_anchor_usd=session.get("polycop_available_usd"),
        cash_anchor_at=float(session.get("polycop_sync_at") or 0),
        eval_since_ts=None,
        eval_baseline_usd=None,
    )
    if state.get("account_total_usd") is None:
        return JSONResponse({"ok": False, "error": "Brak salda — Sync PolyCop"}, status_code=400)

    now = datetime.now(timezone.utc).timestamp()
    baseline = state["account_total_usd"]
    session = {
        **session,
        "active": True,
        "started_at": now,
        "eval_baseline_usd": baseline,
    }
    save_session(session)
    return {"ok": True, "already_active": False, "session": session, "eval_baseline_usd": baseline}


@app.post("/api/eval/stop")
async def eval_stop():
    session = load_session()
    session["active"] = False
    save_session(session)
    return {"ok": True, "session": session}


@app.post("/api/balance/sync")
async def balance_sync(request: Request):
    """Sync from PolyCop Wallet screen: Available + Total Balance."""
    body = await request.json()
    try:
        available = round(float(body.get("available_cash") or body.get("available") or body.get("balance") or 0), 2)
        total = body.get("total_balance") or body.get("total")
        total_f = round(float(total), 2) if total is not None else None
    except (TypeError, ValueError):
        return JSONResponse({"ok": False, "error": "invalid numbers"}, status_code=400)

    if available < 0:
        return JSONResponse({"ok": False, "error": "available must be >= 0"}, status_code=400)

    now = datetime.now(timezone.utc).timestamp()
    session = load_session()
    session["polycop_available_usd"] = available
    session["polycop_sync_at"] = now
    if total_f is not None:
        session["polycop_total_usd"] = total_f
    save_session(session)

    wallet = load_monitor().get("poly_wallet", "")
    state = await compute_wallet_state(
        wallet,
        cash_anchor_usd=available,
        cash_anchor_at=now,
        eval_since_ts=float(session["started_at"]) if session.get("started_at") else None,
        eval_baseline_usd=float(session["eval_baseline_usd"]) if session.get("eval_baseline_usd") else None,
    )

    return {
        "ok": True,
        "polycop_available_usd": available,
        "polycop_total_usd": total_f,
        "account_total_usd": state.get("account_total_usd"),
        "positions_value_usd": state.get("positions_value_usd"),
    }


@app.post("/api/balance/override")
async def balance_override(request: Request):
    return await balance_sync(request)
