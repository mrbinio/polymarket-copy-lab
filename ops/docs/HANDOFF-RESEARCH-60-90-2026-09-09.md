# Polymarket Copy Lab — Handoff: 60/90d research (2026-09-09)

**Author:** Damian (lab) / agent pass  
**Date:** 2026-09-09  
**Audience:** Damian; Mitch if shared as collaborator  
**Purpose:** Same spirit as `HANDOFF-MITCH-v1.1.md` — what we looked for, what we measured, why the previous “recovery spoon” was wrong, and the only decision that follows.

**Related live truth:** 21–22 Aug 2026 session, cash **$52.66 → ~$39.08**, ledger **−$13.58**, PolyCop **Balance SL** paused copy. Almost all fills were **invorser esport** (LoL / CS / Dota). Dashboard −$10 is an alarm only.

---

## 1. Executive summary

| | Claim |
|--|--|
| **Question** | After the Aug stop, who is copyable at **$5 / ignore &lt; $20 / spend $15** on a **60–90 day** track, globally, not the old Top 8? |
| **Universe** | MONTH + Merlin **706** unique; non-sport sit **503**; ALL-time mid-tier ranks 51–150 **467**. Isolated sims run: **76**. |
| **Independent veto** | CopyGrade public grades + their Sep 2026 “best to copy” shortlist, then **replayed in our lab**. |
| **Verdict** | **No wallet clears all gates.** Copy stays **off**. First spoon is **not** Weasel, **not** Antblack-this-week, **not** CopyGrade #1 (PoppyG). |
| **Next live** | Do **not** Turn On All Copy. If anything is ever enabled later: **1** wallet, spend **$15**, Max Yes/No **$5**, new Balance SL **~$31**. |

**Bottom line:** A positive 30d scout or a MONTH leaderboard name is not a strategy. The copier’s edge after our caps, a 60/90 window, concentration, 5-minute-candle filter, and CopyGrade farming/Avoid is a different question — and right now the answer is empty.

---

## 2. What was wrong with the previous recovery plan

The 9 Sep “step 2” recycled **roster-top8** and 30-day scout heat.

| Name | Why it was not a 60/90d answer |
|------|--------------------------------|
| Weaseloftheweek | 60d isolated sim **$0** (3 copies under $15 spend). 90d **+$5.6**. |
| Lucas1tb | ~11d history, high tpd — streak, not a track. |
| invorser | Live loss. Permanent ban. |
| disputequant / 0xwise / PotatoKotato / Bertapotamous | CopyGrade **Avoid** and/or **severe farming**. |

---

## 3. Method (the living system)

Four layers. Research tools **propose**. The lab **decides if a $5 copier would have been paid**. PolyCop is the **only executor**. Live cash is **truth**.

1. **Discover** — Polymarket Data API category boards (`MONTH` and `ALL`), Merlin `/api/leaderboard`, CopyGrade shortlist + `GET https://copygrade.com/api/v1/wallets/{addr}`.
2. **Hard gates** — history ≥60d (prefer 90d), ≤15 trades/day, month PnL ≤ $250k, ban invorser, ban Bitcoin/crypto **5-minute Up or Down** candles.
3. **Lab** — isolated replay: Fixed $5, ignore &lt; $20, prices 0.15–0.85, spend $15, Max Yes/No $5. Require **both** 60d and 90d sim &gt; 0, ≥6 copies, top market ≲ 40% of \|PnL\|.
4. **Veto** — CopyGrade Avoid / severe farm / negative post-fee edge. CopyGrade itself publishes that the score is **not validated** against forward copier returns.
5. **Execute (later only)** — PolyCop Telegram. Never FrenFlow / Poly Syncer / PolyTrackers as a second bot.

**Tools that exist but were not plugged in as executors**

| Tool | What happened |
|------|----------------|
| PolyTrackers OpenAPI/MCP | Present; **agent API key** required; has trade-execute routes → out of scope |
| FrenFlow / Poly Syncer | No public leaderboard JSON; they **are** copy bots |
| Wallet Master / PolyMart / PredictFolio | Paywalled / login; not invented as scores |
| CopyGrade MCP | Same payload as HTTP; this Cursor session used HTTP (IP budget 20/min) |

---

## 4. Funnel (numbers)

### MONTH + Merlin

| Step | Count |
|------|------:|
| Unique wallets | 706 |
| Dropped history &lt; 60d | 409 |
| Dropped month whale &gt; $250k | 26 |
| Passed 60d and tpd ≤ 15 | 268 |
| Simulated (PnL-sorted — **sports-biased**) | 40 |
| 60d **and** 90d sim &gt; 0, ≥6 copies, Yes/No ≤ $5 | 7 (all sport) |

### Non-sport MONTH (stratified, 6 sims / category)

503 unique → 200 with 60d → **36** simulated → **8** with both windows &gt; 0.

### ALL-time mid-tier (offset 50 + 100, skip mega-whales)

467 unique → 242 with ≥90d → **24** simulated (4 / category) → **2** with both windows &gt; 0 (both one-theme politics, thin copier sample).

**Honest gap:** 268 MONTH survivors were not all simulated. First 40 were PnL-sorted. Non-sport and ALL-time passes exist to correct that bias, not to pretend we backtested every wallet on Polygon.

---

## 5. Independent tool vs old roster

CopyGrade grades the **leader’s copier** after fees/latency, not our $5 filter.

| Wallet | CopyGrade | Farming | Real edge | Lab 60d | Call |
|--------|-----------|---------|-----------|---------|------|
| invorser | not indexed | — | — | live −$13.58 | Permanent ban |
| PotatoKotato | 30 Avoid | severe, 16 | −10.6% | esport stack | Ban |
| 0xwise | 30 Avoid | severe, 2 | −3.3% | Merlin whale | Ban |
| Bertapotamous | 30 Avoid | severe, 3 | +2.3% | — | Ban |
| disputequant | 40 Avoid | watch, 1 | **−56.3%** | not a 60d passer | Ban as first spoon |
| gambamaster | 37 Avoid | watch, 2 | **−100%** | +$23.9 | Lab likes filter; **tool veto** |
| Weaseloftheweek | (rate limit on retry) | — | — | **$0** | Not a 60d copy |
| Antblack | 64 caution | watch, 1 | +23.2% | +$17.0 / 13 markets | Only indexed name not Avoid — **still too young** |

---

## 6. CopyGrade “best to copy” (Sep 2026) — lab replay

Their shortlist median **days observed = 30**. That is the opposite of a 60–90d track. Full addresses from `copygrade.com/wallet/…`.

| Name | History | tpd | 60d sim | Why it fails **our** gates |
|------|--------:|----:|---------|----------------------------|
| PoppyG (95) | 185d | 2.7 | $0 | **81% Bitcoin 5-minute Up or Down** |
| phatsddds125 (94) | 69d | 7.2 | $0 | Almost nothing copyable under ignore $20 |
| Misty-Notoriety-Manager (94) | 63d | 7.9 | +$5.7 | **99% candles** |
| PlyQuants (93) | **25d** | **20.3** | +$2.0 | Exact streak pattern we already burned on |
| ziiizar01 (93) | 106d | 4.7 | $0 | $5 copier not paid |
| BuBu12 (92) | 137d | 3.6 | 0 copies | ignore $20 |
| Ringed-Laparoscope (92) | 227d | 1.3 | $0 | Longest of their top; still $0 under caps |

---

## 7. Lab “passers” after concentration

Positive 60/90 sim is luck if one market or one candle family paid it.

- **Sport 7:** Jittz / gambamaster / 0x760f1063 — World Cup date cluster. martingaleking — NBA, CopyGrade Avoid −42% real edge. Antblack — tennis, concentration **0.11**, 76d, farming watch. b324u — 192d but 60d is a few headline copies.
- **Non-sport 8:** Bromsloy142515 — 522d, +$53, **100% Bitcoin Up or Down**. Gabriell11 / 0xc938 — GTA VI view-count **same event family**. BrownBees — 100% one Claude market. 5038813185 — CopyGrade **severe farm, 8 signals**. jacky00 — 55% one Astra market.

**ALL-time politics:** 86shin (220d, Thai election theme, thin 60d copier sample); Llalalala (480d, one ceasefire copy). Not a book.

---

## 8. Gates before we even discuss enable

| Gate | Bar | Who clears |
|------|-----|------------|
| Track | ≥90d preferred | Several; not the bottleneck |
| Speed | ≤15 tpd; 0% 5-minute Up or Down | Fails PoppyG, Misty, Bromsloy |
| Our copier | 60d and 90d sim &gt; 0, ≥6 copies, Yes/No ≤ $5 | Sport 7 + culture cluster; most CG stars fail |
| Not one market | Top market ≲ 40%, ≥5 markets | Antblack yes; almost everyone else no |
| Independent veto | Not Avoid / not severe farm / not −edge | Antblack is caution+watch |
| Category | Not correlated esport after Aug 22 | Antblack is tennis |
| **All at once** | Required for PolyCop Active | **None** |

---

## 9. Decision (pending Damian OK — recommended)

| Action | Rationale |
|--------|-----------|
| **Keep copy paused** | Empty intersection of gates |
| **Do not** enable Antblack this week | 76d + farming watch after a −$13 live hit |
| **Do not** enable 86shin | Thin 60d copier sample; one election theme |
| Re-run this funnel weekly | Living system, not a one-off list |
| If ever enable | 1 wallet, spend $15, Max Yes/No $5, Balance SL ~$31, never Turn On All Copy |

Old Balance SL **$42** is stale at ~$39 cash.

---

## 10. Watch only (not Active)

```
Antblack   0x9c68e5a4b3004bf6ac9d6735a33ffb5f983a247a   tennis     76d   60d +$17.0 / 90d +$9.2
86shin     0x1cfc6f041b5b8dad3c14b867be8482a69d45b8e0   politics   220d  60d +$9.3 / 90d +$9.3
```

---

## 11. Artifacts

| File | What |
|------|------|
| `data/deep_screen.json` | MONTH + Merlin funnel, 40 sims, 7 passers |
| `data/deep_screen_nonsport.json` | Stratified non-sport 36 sims |
| `data/cross_tool_research.json` | CopyGrade grades, concentration, ALL-time 24 |
| `data/copygrade_extra.json` | Lab replay of CG shortlist addresses |
| `data/recovery-strategy-v3.json` | Machine-readable verdict |
| `ops/data/snapshot.json` | Sanitized snapshot for the ops site |
| Canvas | research board beside chat (not the remote site) |

`data/*` stays gitignored except what we export into `ops/data/`.

---

## 12. Changelog (Mitch-style)

```
2026-08-15  v1.0 live — 8 wallets, spend $40, seed ~$53
2026-08-16  v1.0 stop — ~+$156 settled
2026-08-16  v1.1 live — 4 active esport-heavy
2026-08-17  v1.1 CLOSED — +$15.94; Stop All Copy during LoL
2026-08-21  go-live Top 8; only 3 Active (Weasel, dispute, invorser)
2026-08-22  Balance SL; cash ~$39.08; −$13.58; invorser esport night
2026-09-09  60/90d global screen + CopyGrade veto → NO enable
2026-09-09  Ops command center started (auth-gated site + this handoff)
```

---

*End of handoff — research 60/90d, 2026-09-09. Copy off.*
