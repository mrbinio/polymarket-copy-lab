#!/usr/bin/env python3
"""Print PolyCop setup checklist and roster addresses from genome."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--config",
        default="v1.0.0.json",
        help="genome file under configs/ (default: v1.0.0.json)",
    )
    args = parser.parse_args()

    genome_path = ROOT / "configs" / args.config
    genome = json.loads(genome_path.read_text())
    cs = genome["copy_settings"]
    bankroll = genome.get("bankroll_usd", cs["total_spend_limit_usd"] + 10)
    monitor_path = ROOT / "configs" / "monitor.json"
    stop_loss = 10.0
    if monitor_path.exists():
        stop_loss = float(json.loads(monitor_path.read_text()).get("stop_loss_usd") or 10)

    print(f"=== {genome.get('version', genome_path.stem)} ===")
    print(genome.get("notes", ""))
    print()
    print("=== PolyCop settings (EACH wallet — same values) ===")
    print(f"Fixed Amount:        ${cs['fixed_amount_usd']:.0f}")
    print(f"Max Per Trade:       ${cs['max_per_trade_usd']:.0f}")
    print(f"Max Per Yes/No:      ${cs['max_per_yes_no_usd']:.0f}  ← CRITICAL")
    print(f"Max Per Market:      ${cs['max_per_yes_no_usd']:.0f}  (recommended)")
    print(f"Ignore trades <:     ${cs['ignore_below_usd']:.0f}")
    print(f"Min / Max price:     {cs['min_price']} – {cs['max_price']}")
    print(f"Total Spend Limit:   ${cs['total_spend_limit_usd']:.0f}  (seed ~${bankroll:.0f} − $10 buffer)")
    print(f"Copy Buy + Sell:     ON")
    print(f"Auto Redeem:         ON")
    print(f"Reverse copy:        OFF")
    print()
    print("=== Roster (Create Copy Trade × N, paste address each time) ===")
    for i, w in enumerate(genome["target_wallets"], 1):
        print(f"{i}. {w.get('username') or 'wallet'}")
        print(f"   {w['address']}")
    print()
    print("=== v1.0 workflow ===")
    print(f"1. PolyCop: Stop All Copy gdy sesja ≤ −${stop_loss:.0f}")
    print("2. Czekaj 0 open pozycji → sync Wallet w panelu → Start eval → Turn On All Copy (8× ✅)")
    print()
    print("PolyCop: usuń/wstrzymaj obecne portfele → dodaj powyższe 8 → ustaw jak wyżej.")
    print("Gdy 0 otwartych pozycji: Turn On All Copy (8× ✅). Potem dashboard Start eval.")


if __name__ == "__main__":
    main()
