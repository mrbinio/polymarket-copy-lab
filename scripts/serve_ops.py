#!/usr/bin/env python3
"""Serve repo root so /ops/ and /docs/ work together."""

from __future__ import annotations

import http.server
import os
import socketserver
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PORT = int(os.environ.get("OPS_PORT", "8788"))
HOST = os.environ.get("OPS_HOST", "0.0.0.0")


class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT), **kwargs)


if __name__ == "__main__":
    os.chdir(ROOT)
    with socketserver.TCPServer((HOST, PORT), Handler) as httpd:
        print(f"Ops console  http://127.0.0.1:{PORT}/ops/", flush=True)
        print(f"LAN          http://0.0.0.0:{PORT}/ops/  (all interfaces)", flush=True)
        httpd.serve_forever()
