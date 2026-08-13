#!/usr/bin/env python3
"""Run live dashboard on http://0.0.0.0:8765 (dostepny tez z telefonu w tej samej sieci Wi-Fi)."""

import uvicorn

if __name__ == "__main__":
    uvicorn.run("dashboard.live_app:app", host="0.0.0.0", port=8765, reload=True)
