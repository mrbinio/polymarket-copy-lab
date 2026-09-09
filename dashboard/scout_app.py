"""Scout dashboard — roster win/loss table + auto-discovery (:8767)."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from dashboard.scout_engine import engine

ROOT = Path(__file__).resolve().parent.parent
SCOUT_CONFIG_PATH = ROOT / "configs" / "scout_dashboard.json"

app = FastAPI(title="Polymarket Copy Lab — Scout")
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


def load_scout_config() -> dict:
    if SCOUT_CONFIG_PATH.exists():
        return json.loads(SCOUT_CONFIG_PATH.read_text())
    return {}


@app.middleware("http")
async def access_key_guard(request: Request, call_next):
    key = load_scout_config().get("dashboard_key")
    if not key:
        return await call_next(request)
    supplied = request.query_params.get("key") or request.cookies.get("dash_key")
    if supplied != key:
        return JSONResponse({"error": "unauthorized — open /?key=YOUR_KEY"}, status_code=403)
    response = await call_next(request)
    if request.query_params.get("key") == key:
        response.set_cookie("dash_key", key, max_age=365 * 24 * 3600, httponly=True)
    return response


@app.on_event("startup")
async def startup() -> None:
    asyncio.create_task(engine.run_loop())


@app.on_event("shutdown")
async def shutdown() -> None:
    engine.stop_loop()


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse(
        "scout.html",
        {"request": request, "scout_config": load_scout_config()},
    )


@app.get("/api/status")
async def status():
    return engine.snapshot()


@app.post("/api/scout/run")
async def scout_run():
    result = await engine.run_once(refresh=True, scan=True)
    return {"ok": True, **result, **engine.snapshot()}


@app.post("/api/roster/promote")
async def roster_promote(request: Request):
    body = await request.json()
    wallet = body.get("wallet", "")
    if not wallet:
        return JSONResponse({"ok": False, "error": "wallet required"}, status_code=400)
    ok = await engine.promote_to_roster(wallet)
    if not ok:
        return JSONResponse({"ok": False, "error": "not in discovered"}, status_code=404)
    return {"ok": True, **engine.snapshot()}


@app.post("/api/roster/remove")
async def roster_remove(request: Request):
    body = await request.json()
    wallet = body.get("wallet", "")
    if not wallet:
        return JSONResponse({"ok": False, "error": "wallet required"}, status_code=400)
    ok = await engine.remove_from_roster(wallet)
    if not ok:
        return JSONResponse({"ok": False, "error": "not in roster"}, status_code=404)
    return {"ok": True, **engine.snapshot()}
