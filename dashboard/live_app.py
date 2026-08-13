"""Live monitoring dashboard — session settlements + in-window PnL."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from dashboard.polymarket_api import fetch_all_closed_since, settlement_from_closed

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "configs" / "monitor.json"
SESSION_PATH = ROOT / "data" / "session_state.json"

app = FastAPI(title="Polymarket Copy Lab")
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


def load_monitor() -> dict:
    if CONFIG_PATH.exists():
        return json.loads(CONFIG_PATH.read_text())
    example = ROOT / "configs" / "monitor.example.json"
    return json.loads(example.read_text())


def load_session() -> dict:
    if SESSION_PATH.exists():
        return json.loads(SESSION_PATH.read_text())
    return {"active": False, "started_at": None, "baseline_cash_usd": None}


def save_session(state: dict) -> None:
    SESSION_PATH.parent.mkdir(parents=True, exist_ok=True)
    SESSION_PATH.write_text(json.dumps(state, indent=2))


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    monitor = load_monitor()
    session = load_session()
    return templates.TemplateResponse(
        "index.html",
        {"request": request, "monitor": monitor, "session": session},
    )


@app.get("/api/status")
async def status():
    monitor = load_monitor()
    session = load_session()
    wallet = monitor.get("poly_wallet", "")

    settlements: list[dict] = []
    in_window_net = 0.0

    if wallet and not wallet.startswith("0xYOUR"):
        since_ts = 0.0
        if session.get("active") and session.get("started_at"):
            since_ts = float(session["started_at"])

        try:
            closed = await fetch_all_closed_since(wallet, since_ts)
            settlements = [settlement_from_closed(r) for r in closed]
            in_window_net = round(sum(s["pnl"] for s in settlements), 2)
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"error": str(exc), "session": session}, status_code=502)

    tz_name = monitor.get("display_timezone", "Europe/Warsaw")
    tz = ZoneInfo(tz_name)

    for s in settlements:
        if s.get("raw_ts"):
            s["time_local"] = (
                datetime.fromtimestamp(s["raw_ts"], tz=timezone.utc)
                .astimezone(tz)
                .strftime("%H:%M")
            )

    return {
        "session_active": session.get("active", False),
        "started_at": session.get("started_at"),
        "baseline_cash_usd": session.get("baseline_cash_usd"),
        "resolved_count": len(settlements),
        "in_window_net": in_window_net,
        "settlements": settlements,
        "wallet_configured": bool(wallet and not wallet.startswith("0xYOUR")),
    }


@app.post("/api/eval/start")
async def eval_start():
    monitor = load_monitor()
    session = {
        "active": True,
        "started_at": datetime.now(timezone.utc).timestamp(),
        "baseline_cash_usd": monitor.get("cash_balance_usd"),
    }
    save_session(session)
    return {"ok": True, "session": session}


@app.post("/api/eval/stop")
async def eval_stop():
    session = load_session()
    session["active"] = False
    save_session(session)
    return {"ok": True, "session": session}
