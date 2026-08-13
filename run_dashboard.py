#!/usr/bin/env python3
"""Run live dashboard on http://127.0.0.1:8765"""

import uvicorn

if __name__ == "__main__":
    uvicorn.run("dashboard.live_app:app", host="127.0.0.1", port=8765, reload=True)
