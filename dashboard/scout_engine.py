"""Scout dashboard engine — roster tracking + auto-discovery."""

from __future__ import annotations

import asyncio
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from dashboard.backtest_engine import (
    CopySettings,
    fetch_closed_positions,
    fetch_trades,
    run_isolated_backtest,
    settle_positions,
    simulate_copies,
)
from dashboard.backtest_engine import _ts as trade_ts

ROOT = Path(__file__).resolve().parent.parent
STATE_PATH = ROOT / "data" / "scout_dashboard_state.json"
RECOVERY_PATH = ROOT / "data" / "recovery_analysis.json"
SCOUT_CONFIG_PATH = ROOT / "configs" / "scout_dashboard.json"
DATA_API = "https://data-api.polymarket.com"

ESPORT = re.compile(
    r"lol|league|dota|cs2|csgo|counter-strike|valorant|esport|bo3|bo5|lck|lpl|"
    r"iem|blast|game \d|handicap|vct",
    re.I,
)
SPORT = re.compile(
    r"fc | vs\.| vs |win on 20|ufc|nba|nfl|mlb|open|atp|wta|premier|draw\?|o/u|spread:",
    re.I,
)


def classify_domain(title: str) -> str:
    if ESPORT.search(title):
        return "esport"
    if SPORT.search(title):
        return "sport"
    return "other"


def performance_label(sim_pnl: float, wins: int, losses: int) -> str:
    if sim_pnl <= 0 or (losses >= 2 and losses > wins):
        return "przegrywa"
    if sim_pnl > 0 and losses == 0:
        return "wygrywa"
    if sim_pnl > 0 and wins > losses:
        return "wygrywa"
    return "uwaga"


class ScoutEngine:
    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._running = False
        self.state: dict[str, Any] = self._load_state()
        self.settings = CopySettings(total_spend_limit_usd=40.0)
        cfg = self._load_scout_config()
        self.scan_interval = int(cfg.get("scan_interval_sec") or 3600)
        self.refresh_interval = int(cfg.get("refresh_interval_sec") or 900)
        self.leaderboard_limit = int(cfg.get("leaderboard_limit") or 100)
        self.discovered_limit = int(cfg.get("discovered_limit") or 20)

    def _load_scout_config(self) -> dict:
        if SCOUT_CONFIG_PATH.exists():
            return json.loads(SCOUT_CONFIG_PATH.read_text())
        return {}

    def _load_state(self) -> dict[str, Any]:
        if STATE_PATH.exists():
            return json.loads(STATE_PATH.read_text())
        roster: list[dict] = []
        if RECOVERY_PATH.exists():
            data = json.loads(RECOVERY_PATH.read_text())
            for row in data.get("proposed_roster") or []:
                roster.append(self._row_from_recovery(row))
        return {
            "roster": roster,
            "discovered": [],
            "last_roster_refresh": None,
            "last_scout_scan": None,
            "scout_scan_count": 0,
            "errors": [],
        }

    def _row_from_recovery(self, row: dict) -> dict:
        return {
            "username": row.get("username") or row.get("wallet", "")[:10],
            "wallet": row["wallet"].lower(),
            "domain": row.get("primary") or "other",
            "added_at": datetime.now(timezone.utc).isoformat(),
            "source": "recovery_analysis",
        }

    def save_state(self) -> None:
        STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        STATE_PATH.write_text(json.dumps(self.state, indent=2))

    async def _fetch_trades(self, wallet: str, limit: int = 120) -> list[dict]:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{DATA_API}/trades",
                params={"user": wallet, "limit": limit},
                timeout=30.0,
            )
            resp.raise_for_status()
            data = resp.json()
            return data if isinstance(data, list) else []

    async def _recent_sim(self, wallet: str, days: int = 30) -> dict[str, Any]:
        async with httpx.AsyncClient(headers={"Accept": "application/json"}) as client:
            trades = await fetch_trades(client, wallet, limit=500)
            closed = await fetch_closed_positions(client, wallet, limit=500)
        cutoff = time.time() - days * 86400
        recent = [t for t in trades if trade_ts(t) >= cutoff]
        if not recent:
            return {
                "sim_pnl_30d": 0.0,
                "wins_30d": 0,
                "losses_30d": 0,
                "copies_30d": 0,
                "win_rate_30d": 0.0,
            }
        positions, _ = simulate_copies(recent, self.settings, closed)
        pnl, wins, losses, _, _ = settle_positions(positions, closed)
        settled = wins + losses
        return {
            "sim_pnl_30d": pnl,
            "wins_30d": wins,
            "losses_30d": losses,
            "copies_30d": len(positions),
            "win_rate_30d": round(100 * wins / max(settled, 1), 0),
        }

    async def _wallet_metrics(self, wallet: str, username: str) -> dict[str, Any]:
        bt = await run_isolated_backtest(wallet, username=username, settings=self.settings)
        recent = await self._recent_sim(wallet, 30)
        titles = [str(t.get("title") or "") for t in await self._fetch_trades(wallet)]
        mix = {}
        for t in titles[:200]:
            d = classify_domain(t)
            mix[d] = mix.get(d, 0) + 1
        domain = max(mix, key=mix.get) if mix else "other"
        wins, losses = bt.wins, bt.losses
        settled = wins + losses
        label = performance_label(bt.sim_pnl, wins, losses)
        return {
            "username": username or wallet[:10],
            "wallet": wallet.lower(),
            "sim_pnl": bt.sim_pnl,
            "sim_pnl_30d": recent["sim_pnl_30d"],
            "copies": bt.copy_count,
            "copies_30d": recent["copies_30d"],
            "wins": wins,
            "losses": losses,
            "wins_30d": recent["wins_30d"],
            "losses_30d": recent["losses_30d"],
            "open_sim": bt.open_count,
            "win_rate": round(wins / max(settled, 1), 2),
            "win_rate_pct": round(100 * wins / max(settled, 1), 0),
            "win_rate_30d_pct": recent["win_rate_30d"],
            "wins_per_week": round(wins / max(bt.history_days, 1) * 7, 1),
            "history_days": bt.history_days,
            "tpd": bt.trades_per_day,
            "domain": domain,
            "max_exposure": bt.max_outcome_exposure,
            "status": label,
            "error": bt.error,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }

    async def refresh_roster(self) -> None:
        async with self._lock:
            rows = []
            for item in self.state.get("roster") or []:
                w = item.get("wallet", "")
                if not w:
                    continue
                try:
                    m = await self._wallet_metrics(w, item.get("username", ""))
                    m["added_at"] = item.get("added_at")
                    m["source"] = item.get("source", "manual")
                    rows.append(m)
                except Exception as exc:  # noqa: BLE001
                    self._push_error(f"roster {w[:10]}: {exc}")
                await asyncio.sleep(0.35)
            self.state["roster_live"] = rows
            self.state["last_roster_refresh"] = datetime.now(timezone.utc).isoformat()
            self.save_state()

    async def _fetch_leaderboard(self, limit: int = 50) -> list[dict]:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{DATA_API}/v1/leaderboard",
                params={"window": "30d", "limit": limit},
                timeout=30.0,
            )
            resp.raise_for_status()
            data = resp.json()
            return data if isinstance(data, list) else []

    def _known_wallets(self) -> set[str]:
        out: set[str] = set()
        for key in ("roster", "discovered", "roster_live", "discovered_live"):
            for row in self.state.get(key) or []:
                w = row.get("wallet")
                if w:
                    out.add(w.lower())
        return out

    async def scan_new_wallets(self) -> int:
        known = self._known_wallets()
        added = 0
        new_rows: list[dict] = []
        for old in self.state.get("discovered_live") or []:
            w = old.get("wallet", "")
            if w:
                try:
                    m = await self._wallet_metrics(w, old.get("username", ""))
                    m["discovered_at"] = old.get("discovered_at")
                    m["lb_pnl_30d"] = old.get("lb_pnl_30d")
                    new_rows.append(m)
                except Exception:
                    new_rows.append(old)
                await asyncio.sleep(0.35)
        try:
            lb = await self._fetch_leaderboard(self.leaderboard_limit)
        except Exception as exc:  # noqa: BLE001
            self._push_error(f"leaderboard: {exc}")
            return 0

        for entry in lb:
            wallet = (entry.get("proxyWallet") or entry.get("address") or "").lower()
            if not wallet or wallet in known:
                continue
            username = entry.get("userName") or entry.get("username") or wallet[:10]
            try:
                bt = await run_isolated_backtest(wallet, username=username, settings=self.settings)
            except Exception:
                continue
            if bt.error or bt.copy_count < 6 or bt.sim_pnl <= 0:
                continue
            if bt.max_outcome_exposure > 5.01 or bt.trades_per_day > 15:
                continue
            m = await self._wallet_metrics(wallet, username)
            m["lb_pnl_30d"] = entry.get("pnl") or 0
            m["discovered_at"] = datetime.now(timezone.utc).isoformat()
            new_rows.append(m)
            known.add(wallet)
            added += 1
            if len(new_rows) >= self.discovered_limit:
                break
            await asyncio.sleep(0.4)

        async with self._lock:
            self.state["discovered_live"] = sorted(
                new_rows[: self.discovered_limit],
                key=lambda r: (-r.get("sim_pnl", 0), -r.get("win_rate", 0)),
            )
            self.state["last_scout_scan"] = datetime.now(timezone.utc).isoformat()
            self.state["scout_scan_count"] = int(self.state.get("scout_scan_count") or 0) + 1
            self.save_state()
        return added

    def _push_error(self, msg: str) -> None:
        errs = list(self.state.get("errors") or [])
        errs.insert(0, {"at": datetime.now(timezone.utc).isoformat(), "msg": msg})
        self.state["errors"] = errs[:20]

    async def run_once(self, *, refresh: bool = True, scan: bool = True) -> dict:
        if refresh:
            await self.refresh_roster()
        added = 0
        if scan:
            added = await self.scan_new_wallets()
        return {"added": added}

    async def run_loop(self) -> None:
        if self._running:
            return
        self._running = True
        await asyncio.sleep(3)
        try:
            await self.run_once()
        except Exception as exc:  # noqa: BLE001
            self._push_error(f"startup: {exc}")
            self.save_state()
        last_refresh = time.time()
        last_scan = time.time()
        while self._running:
            await asyncio.sleep(30)
            now = time.time()
            try:
                if now - last_refresh >= self.refresh_interval:
                    await self.refresh_roster()
                    last_refresh = now
                if now - last_scan >= self.scan_interval:
                    await self.scan_new_wallets()
                    last_scan = now
            except Exception as exc:  # noqa: BLE001
                self._push_error(f"loop: {exc}")
                self.save_state()

    def stop_loop(self) -> None:
        self._running = False

    def _build_top8(self, roster: list[dict], discovered: list[dict]) -> list[dict]:
        by_wallet: dict[str, dict] = {}
        for row in roster + discovered:
            w = (row.get("wallet") or "").lower()
            if not w:
                continue
            if w not in by_wallet or (row.get("sim_pnl") or 0) > (by_wallet[w].get("sim_pnl") or 0):
                by_wallet[w] = row
        ranked = sorted(
            by_wallet.values(),
            key=lambda r: (-(r.get("sim_pnl") or 0), -(r.get("win_rate_pct") or 0)),
        )
        return [r for r in ranked if (r.get("sim_pnl") or 0) > 0][:8]

    def snapshot(self) -> dict[str, Any]:
        roster = self.state.get("roster_live") or []
        discovered = self.state.get("discovered_live") or []
        top8 = self._build_top8(roster, discovered)
        winning = sum(1 for r in roster if r.get("status") == "wygrywa")
        losing = sum(1 for r in roster if r.get("status") == "przegrywa")
        return {
            "top8": top8,
            "metrics_help": {
                "sim_pnl": "Symulacja copy v1.0 ($5, spend $40) na całej historii API (~500 trans.)",
                "sim_pnl_30d": "Ta sama symulacja, tylko transakcje z ostatnich 30 dni",
                "win_rate_pct": "Wygrane / (wygrane+przegrane) w symulacji — nie live PolyCop",
                "wins_per_week": "Szacunek: wygrane sim ÷ dni historii × 7",
            },
            "roster": roster,
            "discovered": discovered,
            "roster_summary": {
                "count": len(roster),
                "wygrywa": winning,
                "przegrywa": losing,
                "uwaga": len(roster) - winning - losing,
                "combined_sim_pnl": round(sum(r.get("sim_pnl") or 0 for r in roster), 2),
            },
            "discovered_count": len(discovered),
            "last_roster_refresh": self.state.get("last_roster_refresh"),
            "last_scout_scan": self.state.get("last_scout_scan"),
            "scout_scan_count": self.state.get("scout_scan_count", 0),
            "refresh_interval_sec": self.refresh_interval,
            "discovered_limit": self.discovered_limit,
            "scan_interval_sec": self.scan_interval,
            "errors": self.state.get("errors") or [],
            "settings": self.settings.__dict__,
        }

    async def promote_to_roster(self, wallet: str) -> bool:
        wallet = wallet.lower()
        async with self._lock:
            for row in self.state.get("discovered_live") or []:
                if row.get("wallet") == wallet:
                    self.state.setdefault("roster", []).append(
                        {
                            "username": row.get("username"),
                            "wallet": wallet,
                            "domain": row.get("domain"),
                            "added_at": datetime.now(timezone.utc).isoformat(),
                            "source": "auto_scout",
                        }
                    )
                    self.save_state()
                    await self.refresh_roster()
                    return True
        return False

    async def remove_from_roster(self, wallet: str) -> bool:
        wallet = wallet.lower()
        async with self._lock:
            before = len(self.state.get("roster") or [])
            self.state["roster"] = [
                r for r in (self.state.get("roster") or []) if r.get("wallet") != wallet
            ]
            if len(self.state["roster"]) == before:
                return False
            self.save_state()
            await self.refresh_roster()
            return True


engine = ScoutEngine()
