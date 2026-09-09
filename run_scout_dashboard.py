#!/usr/bin/env python3
"""Run scout dashboard on http://0.0.0.0:8767 (osobny od live :8765 i backtest :8766)."""

import uvicorn

if __name__ == "__main__":
    uvicorn.run("dashboard.scout_app:app", host="0.0.0.0", port=8767, reload=True)
