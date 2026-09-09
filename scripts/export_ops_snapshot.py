#!/usr/bin/env python3
"""Build the committed ops-site snapshot from local research dumps."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "ops" / "data" / "snapshot.json"


def main() -> None:
    v3 = json.loads((ROOT / "data" / "recovery-strategy-v3.json").read_text())
    sport = json.loads((ROOT / "data" / "deep_screen.json").read_text())
    extra = {}
    extra_path = ROOT / "data" / "copygrade_extra.json"
    if extra_path.exists():
        extra = json.loads(extra_path.read_text())

    cg_rows = []
    for _w, g in (json.loads((ROOT / "data" / "cross_tool_research.json").read_text()).get("copygrade_grades") or {}).items():
        if g.get("status") != "ok":
            continue
        cg_rows.append(
            {
                "username": g.get("username"),
                "source": g.get("source"),
                "score": g.get("score"),
                "label": g.get("label"),
                "farming": g.get("farming"),
                "edge_real": g.get("edgeReal"),
            }
        )
    cg_rows.sort(key=lambda r: (r.get("score") is None, -(r.get("score") or 0)))

    cg_lab = []
    for row in extra.get("sims") or []:
        cg_lab.append(
            {
                "username": row.get("username"),
                "history_days": row.get("history_days"),
                "tpd": row.get("tpd"),
                "w60": (row.get("w60") or {}).get("pnl"),
                "w90": (row.get("w90") or {}).get("pnl"),
                "copies": row.get("sim_copies"),
                "candle": (row.get("candle") or {}).get("likely_candle_bot"),
                "updown": (row.get("candle") or {}).get("up_or_down_share"),
                "top_market": ((row.get("concentration") or {}).get("top_market")),
            }
        )

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "as_of": v3.get("as_of"),
        "cash_usd": v3.get("cash_usd"),
        "last_live_pnl_usd": v3.get("last_live_pnl_usd"),
        "stance": v3.get("stance"),
        "verdict": v3.get("verdict"),
        "system": v3.get("system"),
        "universe": v3.get("universe"),
        "caps_if_enabled_later": v3.get("caps_if_enabled_later"),
        "do_not_copy": v3.get("do_not_copy"),
        "watch_not_enable": v3.get("watch_not_enable"),
        "tools_tried": v3.get("tools_tried"),
        "funnel_month": {
            "unique": 706,
            "history_lt_60d": 409,
            "whale_month": 26,
            "history_pass": 268,
            "simulated": 40,
            "both_windows": 7,
        },
        "funnel_nonsport": {"unique": 503, "history_pass": 200, "simulated": 36, "both_windows": 8},
        "funnel_all_time": {"unique": 467, "history_90d": 242, "simulated": 24, "both_windows": 2},
        "copygrade_indexed": cg_rows,
        "copygrade_shortlist_lab": cg_lab,
        "live": {
            "copy": "paused",
            "reason": "Balance SL 21–22 Aug 2026. Almost all fills were invorser esport. Do not Turn On All Copy.",
            "eval_counter": "stale — dashboard may still show baseline $52.66; cash ~$39.08 is truth",
            "balance_sl_old": 42,
            "balance_sl_if_reenabled": 31,
            "executor": "PolyCop only",
        },
        "concentration": [
            {"name": "Antblack", "share": 0.11, "note": "13 tennis markets"},
            {"name": "Bromsloy BTC 5m", "share": 0.12, "note": "100% Up or Down candles"},
            {"name": "0xc938 GTA", "share": 0.36, "note": "view-count cluster"},
            {"name": "Jittz", "share": 0.35, "note": "World Cup dates"},
            {"name": "martingaleking", "share": 0.34, "note": "NBA; CopyGrade Avoid"},
            {"name": "Gabriell11", "share": 0.48, "note": "GTA VI views"},
            {"name": "gambamaster", "share": 0.53, "note": "World Cup; CopyGrade −100%"},
            {"name": "jacky00", "share": 0.55, "note": "one Astra market"},
            {"name": "5038813185", "share": 0.77, "note": "severe farm"},
            {"name": "BrownBees", "share": 1.0, "note": "one Claude market"},
        ],
        "handoffs": [
            {
                "title": "Research 60/90d",
                "date": "2026-09-09",
                "path": "docs/HANDOFF-RESEARCH-60-90-2026-09-09.md",
            },
            {
                "title": "Ops command center",
                "date": "2026-09-09",
                "path": "docs/HANDOFF-OPS-COMMAND-CENTER.md",
            },
            {
                "title": "Mitch v1.1",
                "date": "2026-08-17",
                "path": "docs/HANDOFF-MITCH-v1.1.md",
            },
        ],
        "month_passers_sport": [
            {
                "username": r.get("username"),
                "history_days": r.get("history_days"),
                "w60": (r.get("w60") or {}).get("pnl"),
                "w90": (r.get("w90") or {}).get("pnl"),
                "copies": r.get("sim_copies"),
            }
            for r in (sport.get("passed_60_90") or [])
        ],
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, indent=2))
    docs_out = ROOT / "ops" / "docs"
    docs_out.mkdir(parents=True, exist_ok=True)
    for name in (
        "HANDOFF-MITCH-v1.1.md",
        "HANDOFF-RESEARCH-60-90-2026-09-09.md",
        "HANDOFF-OPS-COMMAND-CENTER.md",
    ):
        src = ROOT / "docs" / name
        if src.exists():
            (docs_out / name).write_text(src.read_text())
    print("wrote", OUT, "bytes", OUT.stat().st_size)


if __name__ == "__main__":
    main()
