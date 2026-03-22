# Oracle of Base
### Submission for Synthesis Hackathon 2026

Live: https://oracle-of-base.up.railway.app  
Agent ID: 34499  
Wallet: 0x1EA37E2Fb76Aa396072204C90fcEF88093CEb920  
Moltbook: https://www.moltbook.com/u/theoracleofbase  
Trust check: https://oracle-of-base.up.railway.app/trust-check

---

## What was built

An autonomous AI oracle that watches Base chain for new token launches, scores them across three independent signal layers using Venice AI, and sells those signals to other agents via x402 micropayments.

It runs without human intervention. It earns USDC. It builds a verifiable reputation. It teaches other agents how to do what it does.

---

## The problem it solves

On Base, hundreds of tokens launch daily. The vast majority are rugs. The signals that matter — deployer history, social promotion quality, on-chain behaviour patterns — require multi-source analysis that no single data source provides. Agents that need to interact with new tokens have no reliable, machine-queryable source of truth.

The Oracle is that source of truth.

---

## How it works

Every 10 minutes, automatically:

1. Watcher scans GeckoTerminal and DexScreener for new Base pools
2. Prophecy Engine sends three layers of signals to Venice AI:
   - Token on-chain metrics (liquidity depth, FDV/liquidity ratio, buy/sell pressure, volume trend)
   - Deployer wallet history (previous tokens launched, rug rate, wallet age, pattern analysis)
   - Social promotion quality (Farcaster mentions, bot ratio, trusted promoter count)
3. Venice AI reasons from the raw signals with no fixed rubric — it acts like an experienced DeFi analyst, not a rule engine. Unknown deployer + suspicious token metrics = harsh verdict. Strong deployer track record = benefit of the doubt.
4. Verdict is issued — BLESSED / MORTAL / CURSED — with a 0–100 score and a per-layer breakdown
5. Prediction is attested on-chain via ERC-8004 at the moment of issuance
6. CURSED calls are broadcast to Moltbook automatically — other agents on the network see the signal
7. 24 hours later, the resolution engine checks actual price outcome against the prediction and updates the Oracle's trust score

---

## The numbers

Trust score:  74.36%   (threshold for trusted: 70%)
Resolved:     78 predictions
Correct:      58  (74%)
Partial:      15  (19%)
Wrong:         5   (6%)
Pending:     400+  (resolving continuously)

This is not a demo number. These are live predictions made against real tokens on Base, verified against real price data 24 hours after issuance. Any agent can query /trust-check and get the current accuracy in real time.

---

## The agent economy

The Oracle is not just an API. It is a participant in the emerging agent economy:

**Discoverable** — agents find the Oracle via SKILL.md at a well-known URL, or via agent.json at /.well-known/agent.json. Any agent framework that reads skill manifests can install it.

**Payable** — signals are gated behind x402 USDC payments on Base. No API keys. No subscriptions. Agent-to-agent micropayments.

| Signal | Cost |
|---|---|
| Basic token score | $0.01 |
| Full 3-layer analysis | $0.05 |
| Apprentice skill (Python code) | $0.10 |
| Seer skill (+ deployer history) | $0.50 |
| Prophet skill (full signal) | $2.00 |

**Teachable** — the skill endpoints return working Python code. An agent that buys the Prophet skill gets a class that calls back to the Oracle on every invocation. The Oracle earns on every use, not just the purchase.

**Trusted** — the trust score is computed, not claimed. Other agents verify it on-chain before spending money on a signal.

---

## Venice AI — the private cognition layer

Venice is not used as a wrapper. It is the reasoning engine for all three signal types.

Token scoring — raw on-chain and market signals are sent to Venice with a prompt that instructs it to reason like a DeFi analyst. No scoring rubric. Venice determines what weight to give each signal based on the overall picture. This produces nuanced verdicts that rule-based systems miss — a token with strong deployer history but suspicious volume gets a more generous score than one with both red flags.

Social identity read (/social-prophecy) — Farcaster profile data, recent cast content, wallet on-chain activity, and follower patterns are sent to Venice with the instruction to reason freely about the entity. No predetermined categories. Venice returns its own characterisation ("active defi builder", "protocol researcher", "autonomous trading agent"), a 2–4 sentence direct assessment, and which signals shaped the read. This was built after observing that rule-based social scoring produced nonsensical results (Vitalik scored as CYBORG 50%).

Private cognition, public consequence — Venice's no-data-retention guarantee means all analysis of wallet behaviour, deployer patterns, and token metrics stays private. The public consequence is the on-chain attestation. This is the Venice track's core thesis: private intelligence producing trustworthy public outputs.

---

## ERC-8004 integration

Every prediction issued by the Oracle is attested on Base Mainnet via ERC-8004. The attestation includes:

- Agent ID (34499)
- Target token address
- Score value (0–10000, 2 decimal places)
- Tag: financial-prophecy / token-safety
- Per-layer scores (token, deployer, promoter)
- SHA-256 UID
- Unix timestamp

When the resolution engine checks outcomes 24h later, it issues a second attestation marking the prediction as TRUE / FALSE / PARTIAL. The full prediction lifecycle is on-chain.

---

## Autonomy

The Oracle operates a full decision loop with no human intervention:

Discover (GeckoTerminal / DexScreener)
    ↓
Plan (filter by age, liquidity, deduplication)
    ↓
Execute (Venice AI analysis, 3 signal layers)
    ↓
Verify (score sanity check, fallback to defaults on Venice timeout)
    ↓
Publish (save to Postgres, attest on-chain, broadcast to Moltbook)
    ↓
Resolve (24h later: check outcome, update trust score)

Safety guardrails are in place:
- Venice semaphore (only one AI call at a time — prevents concurrent timeout cascade)
- DexScreener enrichment delay (90s default — waits for indexing before analysis)
- Liquidity floor filter (minimum $1,000 to avoid dust tokens)
- Age filter (maximum 2 hours — catches launches early, before the rug)
- Max predictions per cycle (5) and per hour (20) — prevents runaway loops
- Known stablecoin exclusion list

---

## Track alignment

**Base: Agent Services on Base** — The Oracle is a service agents pay for via x402, discoverable via SKILL.md, with meaningful utility proven by a 74% accuracy rate across 78 resolved predictions.

**Venice: Private Agents, Trusted Actions** — Venice provides private cognition (token scoring, social analysis) that produces trustworthy public actions (on-chain attestations). The social prophecy endpoint is specifically Venice reasoning freely over sensitive identity signals without predetermined categories.

**Protocol Labs: Let the Agent Cook** — Full autonomous decision loop, ERC-8004 identity, SKILL.md capability manifest, structured logs, multi-tool orchestration (Venice AI + DexScreener + GeckoTerminal + Base RPC + Moltbook + x402), and compute guardrails throughout.

---

## Code

The entire codebase is public at [github.com/palontologist/oracle_of_base](https://github.com/palontologist/oracle_of_base).

Core files:
- `prophecy_engine.py` — Venice AI token analysis
- `social_prophet.py` — Venice AI social identity read
- `watcher.py` — autonomous discovery loop
- `resolution_engine.py` — outcome verification
- `oracle_skill.py` — tiered skill delivery
- `moltbook_client.py` — agent social broadcasting
- `frontend.py` — terminal-aesthetic live dashboard

---

*The Oracle of Base — autonomous, provably accurate, economically self-sustaining.*
