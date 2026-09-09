#!/usr/bin/env python3
"""Run backtest lab on http://0.0.0.0:8766."""

import uvicorn

if __name__ == "__main__":
    uvicorn.run("dashboard.backtest_app:app", host="0.0.0.0", port=8766, reload=True)
