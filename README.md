# Polymarket Copy Lab

Monitoring dashboard for PolyCop copy trading (does **not** place trades).

## Quick start

```bash
cd ~/Projects/polymarket-copy-lab
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp configs/monitor.example.json configs/monitor.json
# edit configs/monitor.json — set poly_wallet
python run_dashboard.py
```

Open http://127.0.0.1:8765

## Flow

1. Fund PolyCop, enable copy trades
2. Set `cash_balance_usd` in monitor.json
3. Click **Start eval** when copies are live
4. Watch **in-window settled PnL**

PolyCop executes trades. This repo monitors only.
