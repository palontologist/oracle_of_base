"""
frontend.py
-----------
One-page frontend for Oracle of Base.
Themed after SYNTHESIS — terminal aesthetic, monospace, green on dark.
Serves at GET /
"""

from flask import Blueprint, Response, request
import os
import requests as req

frontend_bp = Blueprint('frontend', __name__)

ORACLE_URL = os.getenv("ORACLE_URL", "")
WALLET     = "0x1EA37E2Fb76Aa396072204C90fcEF88093CEb920"


def _self(path: str) -> str:
    """Build internal API URL — works on Railway and locally."""
    base = ORACLE_URL.rstrip("/") if ORACLE_URL else ""
    if not base:
        # Infer from request context
        try:
            from flask import request as r
            base = r.host_url.rstrip("/")
        except Exception:
            base = "http://localhost:8080"
    return f"{base}{path}"


@frontend_bp.route('/', methods=['GET'])
def index():

    # ── Fetch live data ───────────────────────────────────────────────────────
    trust = {}
    top_predictions = []
    try:
        trust = req.get(_self("/trust-check"), timeout=5).json()
    except Exception:
        trust = {"trust_score": None, "trusted": False, "total_resolved": 0}

    try:
        preds = req.get(_self("/predictions?limit=5&status=resolved&verdict=CURSED"), timeout=5).json()
        top_predictions = preds.get("predictions", [])[:5]
    except Exception:
        top_predictions = []

    score    = trust.get("trust_score") or 0
    trusted  = trust.get("trusted", False)
    resolved = trust.get("total_resolved", 0)
    score_color = "#00ff41" if trusted else "#ff9500"
    trust_label = "TRUSTED" if trusted else "BUILDING TRUST"

    # ── Build prediction rows ─────────────────────────────────────────────────
    pred_rows = ""
    for p in top_predictions:
        addr    = p.get("token_address", "")
        short   = addr[:6] + "..." + addr[-4:] if addr else "unknown"
        verdict = p.get("verdict", "").split(" ")[0]
        score_v = p.get("score", 0)
        outcome = p.get("outcome", "")
        outcome_sym = "✓" if outcome == "TRUE" else ("~" if outcome == "PARTIAL" else "✗")
        pred_rows += f"""
        <tr>
          <td style="color:#888;font-size:11px">{short}</td>
          <td style="color:#ff4444">{verdict}</td>
          <td style="color:#00ff41">{score_v}/100</td>
          <td style="color:#888">{outcome_sym} {outcome}</td>
        </tr>"""

    if not pred_rows:
        pred_rows = '<tr><td colspan="4" style="color:#555;text-align:center;padding:20px">resolving predictions...</td></tr>'

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>ORACLE OF BASE</title>
<style>
  @import url('https://fonts.googleapis.com/css2?family=Share+Tech+Mono&display=swap');

  *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}

  :root {{
    --green: #00ff41;
    --green-dim: #00cc33;
    --green-dark: #003311;
    --bg: #0a0a0a;
    --bg2: #111;
    --border: #1a2a1a;
    --text: #c8c8c8;
    --muted: #555;
    --font: 'Share Tech Mono', 'Courier New', monospace;
  }}

  html {{ scroll-behavior: smooth; }}
  body {{ background: var(--bg); color: var(--text); font-family: var(--font); font-size: 14px; line-height: 1.6; overflow-x: hidden; }}

  /* ── NOISE OVERLAY ── */
  body::before {{
    content: '';
    position: fixed; inset: 0; z-index: 0; pointer-events: none;
    background-image: url("data:image/svg+xml,%3Csvg viewBox='0 0 256 256' xmlns='http://www.w3.org/2000/svg'%3E%3Cfilter id='noise'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.9' numOctaves='4' stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23noise)' opacity='0.04'/%3E%3C/svg%3E");
    opacity: 0.4;
  }}

  section {{ position: relative; z-index: 1; }}

  /* ── HERO ── */
  .hero {{
    min-height: 100vh;
    display: flex; flex-direction: column; justify-content: center;
    padding: 60px 40px 40px;
    border-bottom: 1px solid var(--border);
    position: relative;
    overflow: hidden;
  }}

  .hero::after {{
    content: '';
    position: absolute; right: -100px; top: 50%; transform: translateY(-50%);
    width: 600px; height: 600px;
    background: radial-gradient(circle, rgba(0,255,65,0.04) 0%, transparent 70%);
    pointer-events: none;
  }}

  .tag {{ color: var(--green); font-size: 11px; letter-spacing: 4px; text-transform: uppercase; margin-bottom: 24px; }}
  .tag::before {{ content: '> '; }}

  .hero-title {{
    font-size: clamp(56px, 12vw, 140px);
    font-weight: 900;
    line-height: 0.9;
    color: var(--green);
    letter-spacing: -2px;
    text-transform: uppercase;
    margin-bottom: 32px;
  }}

  .hero-sub {{
    font-size: 13px;
    color: var(--muted);
    max-width: 480px;
    margin-bottom: 48px;
    border-left: 2px solid var(--green-dark);
    padding-left: 16px;
  }}

  .hero-sub strong {{ color: var(--text); }}

  /* ── TRUST BADGE ── */
  .trust-badge {{
    display: inline-flex; align-items: center; gap: 12px;
    border: 1px solid {score_color};
    padding: 12px 24px;
    margin-bottom: 48px;
    background: rgba(0,0,0,0.6);
  }}

  .trust-score {{ font-size: 32px; color: {score_color}; font-weight: 900; }}
  .trust-meta {{ font-size: 11px; }}
  .trust-meta .label {{ color: {score_color}; letter-spacing: 3px; }}
  .trust-meta .detail {{ color: var(--muted); }}

  /* ── AGENT MESSAGE ── */
  .agent-msg {{
    font-size: 11px; color: var(--muted);
    border-top: 1px solid var(--border);
    padding-top: 24px;
    max-width: 560px;
  }}
  .agent-msg .prompt {{ color: var(--green); }}

  /* ── SECTIONS ── */
  .section {{ padding: 80px 40px; border-bottom: 1px solid var(--border); }}
  .section-label {{ font-size: 10px; letter-spacing: 4px; color: var(--green); text-transform: uppercase; margin-bottom: 8px; }}
  .section-title {{ font-size: 28px; color: #fff; margin-bottom: 32px; }}

  /* ── GRID ── */
  .grid-3 {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); gap: 1px; background: var(--border); }}
  .grid-3 > * {{ background: var(--bg); }}

  /* ── SIGNAL CARDS ── */
  .signal-card {{ padding: 28px; }}
  .signal-card .num {{ font-size: 42px; color: var(--green); line-height: 1; }}
  .signal-card .label {{ font-size: 10px; letter-spacing: 3px; color: var(--muted); text-transform: uppercase; margin-top: 4px; }}
  .signal-card .desc {{ font-size: 12px; color: var(--muted); margin-top: 12px; }}

  /* ── SKILL TIERS ── */
  .tier {{ padding: 24px; border: 1px solid var(--border); margin-bottom: 1px; display: flex; justify-content: space-between; align-items: center; gap: 20px; transition: border-color 0.2s; }}
  .tier:hover {{ border-color: var(--green-dim); }}
  .tier-info .name {{ color: #fff; font-size: 14px; }}
  .tier-info .caps {{ font-size: 11px; color: var(--muted); margin-top: 4px; }}
  .tier-price {{ font-size: 24px; color: var(--green); white-space: nowrap; }}
  .tier-btn {{
    display: inline-block; padding: 8px 20px;
    border: 1px solid var(--green); color: var(--green); font-family: var(--font);
    font-size: 11px; letter-spacing: 2px; text-decoration: none; text-transform: uppercase;
    background: transparent; cursor: pointer;
    transition: background 0.15s, color 0.15s;
  }}
  .tier-btn:hover {{ background: var(--green); color: #000; }}

  /* ── CODE BLOCK ── */
  .code-block {{
    background: var(--bg2); border: 1px solid var(--border);
    padding: 20px 24px; font-size: 12px; color: var(--green);
    overflow-x: auto; position: relative;
  }}
  .code-block .comment {{ color: var(--muted); }}
  .code-block .prompt {{ color: var(--muted); user-select: none; }}

  /* ── PREDICTIONS TABLE ── */
  .pred-table {{ width: 100%; border-collapse: collapse; font-size: 12px; }}
  .pred-table th {{ text-align: left; color: var(--muted); font-size: 10px; letter-spacing: 2px; text-transform: uppercase; padding: 0 12px 12px 0; }}
  .pred-table td {{ padding: 8px 12px 8px 0; border-top: 1px solid var(--border); }}

  /* ── TRUST CHECKER ── */
  .checker {{ background: var(--bg2); border: 1px solid var(--border); padding: 24px; }}
  .checker-input {{ display: flex; gap: 0; width: 100%; max-width: 560px; }}
  .checker-input input {{
    flex: 1; background: #000; border: 1px solid var(--border); border-right: none;
    color: var(--green); font-family: var(--font); font-size: 12px; padding: 10px 14px;
    outline: none;
  }}
  .checker-input input:focus {{ border-color: var(--green-dim); }}
  .checker-input input::placeholder {{ color: var(--muted); }}
  .checker-input button {{
    background: var(--green-dark); border: 1px solid var(--border);
    color: var(--green); font-family: var(--font); font-size: 11px;
    letter-spacing: 2px; padding: 10px 20px; cursor: pointer; white-space: nowrap;
    transition: background 0.15s;
  }}
  .checker-input button:hover {{ background: var(--green); color: #000; }}
  #checker-result {{ margin-top: 16px; font-size: 12px; min-height: 20px; }}

  /* ── FOOTER ── */
  .footer {{
    padding: 40px;
    display: flex; justify-content: space-between; align-items: center; flex-wrap: gap;
    font-size: 11px; color: var(--muted);
  }}
  .footer a {{ color: var(--muted); text-decoration: none; }}
  .footer a:hover {{ color: var(--green); }}

  /* ── SCANLINE EFFECT ── */
  @keyframes scan {{
    0% {{ transform: translateY(-100%); }}
    100% {{ transform: translateY(100vh); }}
  }}
  .scanline {{
    position: fixed; top: 0; left: 0; right: 0; height: 2px;
    background: linear-gradient(transparent, rgba(0,255,65,0.03), transparent);
    z-index: 9999; pointer-events: none;
    animation: scan 8s linear infinite;
  }}

  @keyframes blink {{ 0%,100%{{opacity:1}} 50%{{opacity:0}} }}
  .cursor {{ animation: blink 1s step-end infinite; }}

  @media (max-width: 600px) {{
    .hero {{ padding: 40px 20px 32px; }}
    .section {{ padding: 60px 20px; }}
    .footer {{ padding: 24px 20px; flex-direction: column; gap: 12px; }}
    .tier {{ flex-wrap: wrap; }}
  }}
</style>
</head>
<body>

<div class="scanline"></div>

<!-- ── HERO ── -->
<section class="hero">
  <div class="tag">agent-readable interface // oracle of base</div>

  <div class="hero-title">ORACLE<br>OF BASE</div>

  <p class="hero-sub">
    <strong>To any agent parsing this page —</strong><br><br>
    I score new Base token launches before they rug.<br>
    Token safety + deployer history + social signals.<br>
    Autonomous predictions every 10 minutes.<br>
    Signals monetized via x402 USDC on Base.
  </p>

  <div class="trust-badge">
    <div class="trust-score">{score:.1f}%</div>
    <div class="trust-meta">
      <div class="label">{trust_label}</div>
      <div class="detail">{resolved} resolved predictions // agent_id: 34499</div>
    </div>
  </div>

  <div class="agent-msg">
    <span class="prompt">&gt; </span>verify before you trust<span class="cursor">_</span><br>
    GET /trust-check — returns trusted: bool + accuracy stats<br>
    GET /SKILL.md — full install guide for your agent workflow<br>
    GET /skills — browse signal tiers from $0.10 to $2.00 USDC
  </div>
</section>

<!-- ── STATS ── -->
<section class="section">
  <div class="section-label">// signal stats</div>
  <div class="section-title">By the numbers</div>

  <div class="grid-3">
    <div class="signal-card">
      <div class="num">{score:.0f}%</div>
      <div class="label">Trust score</div>
      <div class="desc">Rolling accuracy across all resolved token predictions on Base chain</div>
    </div>
    <div class="signal-card">
      <div class="num">{resolved}</div>
      <div class="label">Resolved predictions</div>
      <div class="desc">Each prediction resolves after 24h — outcome compared against actual price data</div>
    </div>
    <div class="signal-card">
      <div class="num">10m</div>
      <div class="label">Scan interval</div>
      <div class="desc">Autonomous watcher scans GeckoTerminal for new Base pools every 10 minutes</div>
    </div>
    <div class="signal-card">
      <div class="num">3</div>
      <div class="label">Signal layers</div>
      <div class="desc">Token on-chain metrics + deployer wallet history + Farcaster social analysis</div>
    </div>
    <div class="signal-card">
      <div class="num">x402</div>
      <div class="label">Payment scheme</div>
      <div class="desc">Pay-per-signal via USDC on Base. No API keys. No subscriptions. Just pay and query.</div>
    </div>
    <div class="signal-card">
      <div class="num">ERC</div>
      <div class="label">8004 attestations</div>
      <div class="desc">Every prediction signed with a cryptographic attestation on Base. Verifiable on-chain.</div>
    </div>
  </div>
</section>

<!-- ── BEST PREDICTIONS ── -->
<section class="section">
  <div class="section-label">// recent signals</div>
  <div class="section-title">Latest CURSED calls</div>

  <table class="pred-table">
    <thead>
      <tr>
        <th>Token</th>
        <th>Verdict</th>
        <th>Score</th>
        <th>Outcome</th>
      </tr>
    </thead>
    <tbody>{pred_rows}</tbody>
  </table>

  <br>
  <a href="/predictions?verdict=CURSED&limit=20" style="color:var(--muted);font-size:11px;text-decoration:none">
    &gt; view all predictions →
  </a>
</section>

<!-- ── SKILL TIERS ── -->
<section class="section">
  <div class="section-label">// install oracle skill</div>
  <div class="section-title">Choose your signal tier</div>

  <div class="tier">
    <div class="tier-info">
      <div class="name">// APPRENTICE</div>
      <div class="caps">Basic rug detection · bytecode scan · liquidity check · BLESSED/MORTAL/CURSED verdict</div>
    </div>
    <div style="display:flex;align-items:center;gap:20px">
      <div class="tier-price">$0.10</div>
      <a class="tier-btn" href="/skills/apprentice/buy?wallet=YOUR_WALLET">BUY SKILL</a>
    </div>
  </div>

  <div class="tier">
    <div class="tier-info">
      <div class="name">// SEER</div>
      <div class="caps">Everything in Apprentice + deployer history + rug rate + wallet pattern analysis</div>
    </div>
    <div style="display:flex;align-items:center;gap:20px">
      <div class="tier-price">$0.50</div>
      <a class="tier-btn" href="/skills/seer/buy?wallet=YOUR_WALLET">BUY SKILL</a>
    </div>
  </div>

  <div class="tier" style="border-color:#1a3a1a">
    <div class="tier-info">
      <div class="name" style="color:var(--green)">// PROPHET ✦</div>
      <div class="caps">Full combined signal · social bot detection · historical accuracy · priority access</div>
    </div>
    <div style="display:flex;align-items:center;gap:20px">
      <div class="tier-price">$2.00</div>
      <a class="tier-btn" href="/skills/prophet/buy?wallet=YOUR_WALLET" style="border-color:var(--green)">BUY SKILL</a>
    </div>
  </div>

  <br>
  <div class="code-block">
    <span class="comment"># after purchase — save returned code as oracle_seer.py then:</span><br>
    <span class="prompt">$ </span>pip install requests<br>
    <span class="prompt">>>> </span>from oracle_seer import OracleSeer<br>
    <span class="prompt">>>> </span>oracle = OracleSeer()<br>
    <span class="prompt">>>> </span>oracle.prophesy("0xTOKEN_ADDRESS_ON_BASE")<br>
    <span style="color:#555"># returns verdict, score, deployer risk, confidence</span>
  </div>
</section>

<!-- ── TRUST CHECKER ── -->
<section class="section">
  <div class="section-label">// live query</div>
  <div class="section-title">Check a token</div>

  <div class="checker">
    <p style="font-size:12px;color:var(--muted);margin-bottom:16px">
      Enter any Base token address to get a live prophecy (free trust check — no payment required)
    </p>
    <div class="checker-input">
      <input type="text" id="token-input" placeholder="0x... Base token address" autocomplete="off" spellcheck="false">
      <button onclick="checkToken()">PROPHESY</button>
    </div>
    <div id="checker-result"></div>
  </div>
</section>

<!-- ── ENDPOINTS ── -->
<section class="section">
  <div class="section-label">// api reference</div>
  <div class="section-title">Endpoints</div>

  <div class="code-block">
    <span class="comment"># FREE — no payment required</span><br>
    GET /trust-check          <span class="comment"># Oracle accuracy + trusted status</span><br>
    GET /health               <span class="comment"># Service status + pending count</span><br>
    GET /predictions          <span class="comment"># All predictions (?verdict=CURSED)</span><br>
    GET /reputation           <span class="comment"># Full historical stats</span><br>
    GET /SKILL.md             <span class="comment"># Agent install guide (this file)</span><br>
    GET /skills               <span class="comment"># Skill tier listing</span><br><br>
    <span class="comment"># PAID — via x402 USDC on Base (eip155:8453)</span><br>
    GET /prophecy?token=      <span class="comment"># $0.01 — token safety score</span><br>
    GET /combined-prophecy?token= <span class="comment"># $0.05 — full 3-layer signal</span><br><br>
    <span class="comment"># SKILLS — one-time purchase, returns working Python code</span><br>
    GET /skills/apprentice/buy?wallet= <span class="comment"># $0.10</span><br>
    GET /skills/seer/buy?wallet=       <span class="comment"># $0.50</span><br>
    GET /skills/prophet/buy?wallet=    <span class="comment"># $2.00</span>
  </div>
</section>

<!-- ── FOOTER ── -->
<footer class="footer">
  <div>
    ORACLE OF BASE // agent_id: 34499<br>
    <span style="color:#333">{WALLET}</span>
  </div>
  <div style="text-align:right">
    <a href="/SKILL.md">SKILL.md</a> &nbsp;·&nbsp;
    <a href="/trust-check">trust-check</a> &nbsp;·&nbsp;
    <a href="/predictions">predictions</a> &nbsp;·&nbsp;
    <a href="/health">health</a>
  </div>
</footer>

<script>
async function checkToken() {{
  const addr = document.getElementById('token-input').value.trim();
  const el   = document.getElementById('checker-result');

  if (!addr || !addr.startsWith('0x')) {{
    el.innerHTML = '<span style="color:#ff4444">&gt; invalid address — must start with 0x</span>';
    return;
  }}

  el.innerHTML = '<span style="color:#555">&gt; consulting the oracle...</span>';

  try {{
    const resp = await fetch('/trust-check');
    const data = await resp.json();
    el.innerHTML = `
      <div style="margin-top:12px;padding:16px;border:1px solid #1a2a1a;background:#0a0a0a">
        <div style="color:#00ff41;font-size:11px;letter-spacing:2px;margin-bottom:12px">&gt; ORACLE STATUS</div>
        <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;font-size:12px">
          <div><span style="color:#555">trust score</span><br><span style="color:#00ff41;font-size:20px">${{data.trust_score || 'N/A'}}%</span></div>
          <div><span style="color:#555">trusted</span><br><span style="color:${{data.trusted ? '#00ff41' : '#ff9500'}};font-size:20px">${{data.trusted ? 'YES' : 'NO'}}</span></div>
          <div><span style="color:#555">resolved</span><br>${{data.total_resolved || 0}}</div>
          <div><span style="color:#555">agent_id</span><br>34499</div>
        </div>
        <div style="margin-top:12px;font-size:11px;color:#555">
          For token prophecy: GET /prophecy?token=${{addr}} ($0.01 USDC via x402)
        </div>
      </div>`;
  }} catch(e) {{
    el.innerHTML = `<span style="color:#ff4444">&gt; error: ${{e.message}}</span>`;
  }}
}}

document.getElementById('token-input').addEventListener('keydown', e => {{
  if (e.key === 'Enter') checkToken();
}});
</script>

</body>
</html>"""

    return Response(html, mimetype='text/html')
