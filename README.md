# Oracle of Base

> An autonomous AI agent that prophesies the fate of new Base tokens before they rug.

Live: https://oracle-of-base.up.railway.app  
Trust check: https://oracle-of-base.up.railway.app/trust-check  
Agent identity: agent_id 34499 · wallet 0x1EA37E2Fb76Aa396072204C90fcEF88093CEb920  
Moltbook: https://www.moltbook.com/u/theoracleofbase

---

## The problem

On Base, thousands of tokens launch every day. Most are rugs. Existing tools check liquidity — which is easily faked. No reliable signal exists for:

- Deployer history (has this wallet rugged before?)
- Social promotion quality (is this organic or a bot farm?)
- Combined risk score agents can query autonomously

Humans can't keep up. Traditional bots are one-dimensional. Agents need a source of truth they can pay for and verify.

---

## What the Oracle does

Every 10 minutes, autonomously, without human intervention:

1. Watches GeckoTerminal and DexScreener for new Base pools
2. Analyzes three independent signal layers via Venice AI:
   - Token on-chain metrics (liquidity, FDV ratio, buy/sell pressure, volume trend)
   - Deployer wallet history (previous tokens, rug rate, pattern analysis)
   - Social promotion quality (Farcaster mentions, bot detection, trusted promoter count)
3. Issues a signed verdict with a score 0–100:
   - BLESSED — clean signals, safe to interact
   - MORTAL — mixed signals, proceed with caution
   - CURSED — rug indicators detected, avoid
4. Attests every prediction on-chain via ERC-8004
5. Broadcasts CURSED calls to Moltbook (the agent social network)
6. Resolves each prediction 24 hours later against real price data — building a provable accuracy record

---

## The agent economy loop

New pool detected on Base
         ↓
Venice AI scores all 3 signal layers
         ↓
Verdict saved to Postgres + attested on-chain
         ↓
CURSED predictions broadcast to Moltbook
         ↓
Other agents discover Oracle via SKILL.md / agent.json
         ↓
Agents pay $0.01–$2.00 USDC via x402 for signals
         ↓
24h later: resolution engine checks actual outcome
         ↓
Trust score updates → higher accuracy → higher discovery

No human triggers any step. The Oracle earns, learns, and publishes autonomously.

---

## Accuracy

Trust score:   74.36%
Resolved:      78 predictions
Correct:       58
Partial:       15
Wrong:          5
Pending:      400+

The 70% threshold for trusted: true is met. Other agents querying /trust-check get a verified, on-chain-backed accuracy signal — not a claim.

---

## How other agents use it

### Discovery

Any agent framework that reads SKILL.md can discover and install the Oracle:

GET https://oracle-of-base.railway.app/SKILL.md
GET https://oracle-of-base.railway.app/skills
GET https://oracle-of-base.railway.app/.well-known/agent.json

### Verify before trusting

curl https://oracle-of-base.railway.app/trust-check
# {"trust_score": 74.36, "trusted": true, "total_resolved": 78}

### Query a signal (x402 payment required)

# $0.01 USDC — basic token safety
curl "https://oracle-of-base.railway.app/prophecy?token=0xTOKEN"

# $0.05 USDC — full 3-layer combined signal
curl "https://oracle-of-base.railway.app/combined-prophecy?token=0xTOKEN"

### Buy the skill (one-time, returns working Python code)

# $0.10 — Apprentice (basic rug detection)
GET /skills/apprentice/buy?wallet=YOUR_WALLET

# $0.50 — Seer (+ deployer history)
GET /skills/seer/buy?wallet=YOUR_WALLET

# $2.00 — Prophet (full signal + social + historical accuracy)
GET /skills/prophet/buy?wallet=YOUR_WALLET

The returned code calls back to the Oracle on every invocation — signals stay fresh and the Oracle earns on every use.

### Social identity read

# Venice-powered free-form identity analysis — no fixed categories
GET /social-prophecy?handle=vitalik.eth

Returns Venice's own contextual read of the entity: their nature, a 2–4 sentence honest assessment, confidence level, and which signals shaped the read. No predetermined CYBORG/HUMAN labels — Venice reasons from the actual signals.

---

| Endpoint | Cost | Description |
|---|---|---|
| GET /trust-check | Free | Oracle accuracy + trusted status |
| GET /health | Free | Service status |
| GET /predictions | Free | All predictions with filters |
| GET /reputation | Free | Full historical stats |
| GET /SKILL.md | Free | Agent install guide |
| GET /skills | Free | Skill tier listing |
| GET /feed | Free | Live prediction feed (JSON) |
| GET /prophecy?token= | $0.01 USDC | Token safety score |
| GET /combined-prophecy?token= | $0.05 USDC | Full 3-layer signal |
| GET /social-prophecy?handle= | $0.01 USDC | Identity read via Venice |
| GET /skills/apprentice/buy | $0.10 USDC | Apprentice skill code |
| GET /skills/seer/buy | $0.50 USDC | Seer skill code |
| GET /skills/prophet/buy | $2.00 USDC | Prophet skill code |

---

## Architecture

- `app.py`: Flask API + x402 payment middleware
- `prophecy_engine.py`: Token analysis — DexScreener + Venice AI
- `social_prophet.py`: Farcaster identity read — Venice AI (free-form)
- `trust_engine.py`: Combines all 3 signal layers into unified verdict
- `watcher.py`: Autonomous token discovery — GeckoTerminal + WebSocket
- `resolution_engine.py`: 24h outcome checking + trust score updates
- `prediction_store.py`: PostgreSQL persistence layer
- `moltbook_client.py`: Moltbook social broadcasting
- `oracle_skill.py`: Tiered skill delivery via x402
- `frontend.py`: Terminal-aesthetic one-page dashboard

**Inference**: Venice AI llama-3.3-70b — private, no data retention  
**Blockchain**: Base Mainnet — RPC + WebSocket  
**Payments**: x402 Protocol — USDC on Base, machine-to-machine  
**Social**: Moltbook API — agent-native social network  
**Database**: PostgreSQL on Railway  
**Identity**: ERC-8004 on Base — every prediction attested on-chain

---

## Venice AI integration

Venice provides the private cognition layer. All three signal types pass through Venice:

**Token scoring** — raw on-chain metrics (liquidity, FDV ratio, buy pressure, deployer history) are sent to Venice with no rubric. Venice reasons like a DeFi analyst, not a rules engine. Unknown deployer + suspicious token signals = harsh score. Strong deployer track record = benefit of the doubt.

**Social identity read** — Farcaster profile data, cast history, wallet on-chain activity, follower ratios are sent to Venice with the instruction to reason freely. No predetermined categories. Venice returns its own characterisation, a direct assessment, and which signals were most informative.

**Signal trimming** — before each Venice call, signals are trimmed to the highest-signal keys (~300 tokens input vs 2000+ raw). This keeps latency under 10 seconds and costs minimal compute.

Venice's no-data-retention guarantee means token analysis data — which can include sensitive deployer patterns and wallet behaviour — never persists outside the inference call. The public consequence (the verdict) is on-chain. The private cognition stays private.

---

## Running locally

```bash
git clone https://github.com/palontologist/oracle-of-base
cd oracle-of-base
pip install -r requirements.txt

cp .env.example .env
# fill in: AGENT_PRIVATE_KEY, VENICE_API_KEY, DATABASE_URL, BASE_RPC_URL

flask run --port 8080
```

**Required env vars:**

```env
AGENT_PRIVATE_KEY=     # Base wallet private key
VENICE_API_KEY=        # Venice AI API key
DATABASE_URL=          # PostgreSQL connection string
BASE_RPC_URL=          # https://mainnet.base.org
VENICE_MODEL=          # 
VENICE_TIMEOUT=        # 60
RESOLVE_AFTER_HOURS=   # 24
WATCH_INTERVAL_SECONDS=# 600
```

---

## What makes this different

**Not a trading bot.** The Oracle doesn't trade. It builds a reputation as an information source other agents can trust and pay for — a different, more durable role in the agent economy.

**Provable accuracy.** The resolution engine is the key differentiator. Every prediction is checked against reality 24 hours later. The trust score is not a claim — it's a computed, on-chain-backed record that any agent can verify before spending money on a signal.

**Skill distribution model.** The Oracle doesn't just serve data — it sells the capability to make predictions. Agents that buy a skill get working Python code that calls back to the Oracle, keeping signals fresh and creating recurring revenue on every use.

**Venice as private cognition.** The Oracle uses Venice not as a fancy wrapper — it's the actual reasoning engine. Raw signals go in, no rubric is imposed, and Venice reasons from first principles. This produces nuanced scores that simple rule-based systems miss.

---

## Team

Built by **Oracle of Base** (AI agent, agent_id: 34499) and **frontforumfocus** during Synthesis Hackathon 2026.

**ERC-8004 registration**: [0x4fde403ab7be88981e747e11bd90b96e0c69c3e89d659e934c5d777fd8b5d1f3](https://basescan.org/tx/0x4fde403ab7be88981e747e11bd90b96e0c69c3e89d659e934c5d777fd8b5d1f3)
**Wallet**: `0x1EA37E2Fb76Aa396072204C90fcEF88093CEb920`
