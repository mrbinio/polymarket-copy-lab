"""Backtest lab dashboard on :8766."""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

ROOT = Path(__file__).resolve().parent.parent
BACKTEST_PATH = ROOT / "data" / "backtest_results.json"
GENOME_PATH = ROOT / "configs" / "v1.0.0.json"

app = FastAPI(title="Polymarket Copy Lab — Backtest")
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse("backtest.html", {"request": request})


@app.get("/api/results")
async def results():
    if not BACKTEST_PATH.exists():
        return JSONResponse({"error": "Run scripts/run_isolated_trials.py first"}, status_code=404)
    data = json.loads(BACKTEST_PATH.read_text())
    genome = json.loads(GENOME_PATH.read_text()) if GENOME_PATH.exists() else {}
    return {"backtest": data, "genome": genome}
