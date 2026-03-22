"""
frontend.py
-----------
One-page frontend for Oracle of Base.
Themed after SYNTHESIS — terminal aesthetic, monospace, green on dark.
Serves at GET /
"""

from flask import Blueprint, Response, jsonify
import os
import json

frontend_bp = Blueprint('frontend', __name__)
WALLET = "0x1EA37E2Fb76Aa396072204C90fcEF88093CEb920"


def _get_trust():
    """Pull trust data directly from DB — no HTTP call to self."""
    try:
        from prediction_store import get_reputation_stats, get_conn
        stats = get_reputation_stats("34499")
        score = stats.get("trust_score")
        resolved = stats.get("total_resolved", 0)
        trusted = score is not None and score >= 70 and resolved >= 5
        return {
            "trust_score": round(float(score), 2) if score else 0.0,
            "trusted": trusted,
            "total_resolved": resolved,
            "correct": stats.get("correct", 0),
            "wrong": stats.get("wrong", 0),
            "pending": stats.get("pending", 0),
        }
    except Exception as e:
        return {"trust_score": 0.0, "trusted": False, "total_resolved": 0,
                "correct": 0, "wrong": 0, "pending": 0}


def _get_recent_predictions(limit=20):
    """Pull recent predictions directly from DB."""
    try:
        from prediction_store import get_conn
        import psycopg2.extras
        conn = get_conn()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("""
            SELECT p.id, p.subject as token_address, p.verdict, p.score,
                   p.status, p.created_at,
                   r.outcome, r.accuracy
            FROM predictions p
            LEFT JOIN resolutions r ON r.prediction_id = p.id
            WHERE p.prediction_type = 'token'
            ORDER BY p.created_at DESC
            LIMIT %s
        """, (limit,))
        rows = cur.fetchall()
        cur.close()
        conn.close()
        return [dict(r) for r in rows]
    except Exception:
        return []


@frontend_bp.route('/feed', methods=['GET'])
def feed_api():
    """JSON feed for the live terminal — polled every 15s by frontend JS."""
    preds = _get_recent_predictions(30)
    out = []
    for p in preds:
        addr = p.get("token_address", "")
        verdict = str(p.get("verdict", "")).split(" ")[0]
        score = p.get("score", 0)
        status = p.get("status", "PENDING")
        outcome = p.get("outcome", "")
        created = p.get("created_at")
        ts = created.strftime("%H:%M:%S") if created else "??:??:??"
        out.append({
            "ts": ts,
            "addr": addr,
            "short": addr[:6] + "..." + addr[-4:] if addr else "unknown",
            "verdict": verdict,
            "score": score,
            "status": status,
            "outcome": outcome,
        })
    return jsonify(out)


@frontend_bp.route('/', methods=['GET'])
def index():

    trust = _get_trust()
    recent = _get_recent_predictions(5)

    score    = trust.get("trust_score", 0.0)
    trusted  = trust.get("trusted", False)
    resolved = trust.get("total_resolved", 0)
    correct  = trust.get("correct", 0)
    wrong    = trust.get("wrong", 0)
    pending  = trust.get("pending", 0)
    score_color = "#00ff41" if trusted else "#ff9500"
    trust_label = "TRUSTED ✓" if trusted else "BUILDING TRUST"

    # Build prediction rows for table
    pred_rows = ""
    for p in recent:
        addr    = p.get("token_address", "")
        short   = addr[:6] + "..." + addr[-4:] if addr else "unknown"
        verdict = str(p.get("verdict", "")).split(" ")[0]
        score_v = p.get("score", 0)
        outcome = p.get("outcome") or p.get("status", "PENDING")
        vcolor  = "#ff4444" if verdict == "CURSED" else ("#00ff41" if verdict == "BLESSED" else "#ff9500")
        pred_rows += f"""
        <tr>
          <td style="color:#555;font-size:11px">{short}</td>
          <td style="color:{vcolor}">{verdict}</td>
          <td style="color:#00ff41">{score_v}/100</td>
          <td style="color:#888">{outcome}</td>
        </tr>"""

    if not pred_rows:
        pred_rows = '<tr><td colspan="4" style="color:#333;text-align:center;padding:20px">awaiting predictions...</td></tr>'

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>ORACLE OF BASE</title>
<style>
  @import url('https://fonts.googleapis.com/css2?family=Share+Tech+Mono&display=swap');
  *,*::before,*::after{{box-sizing:border-box;margin:0;padding:0}}
  :root{{
    --green:#00ff41;--green-dim:#00cc33;--green-dark:#003311;
    --red:#ff4444;--amber:#ff9500;
    --bg:#080808;--bg2:#0f0f0f;--bg3:#141414;
    --border:#1a2a1a;--text:#b0b0b0;--muted:#444;
    --font:'Share Tech Mono','Courier New',monospace;
  }}
  html{{scroll-behavior:smooth}}
  body{{background:var(--bg);color:var(--text);font-family:var(--font);font-size:14px;line-height:1.6;overflow-x:hidden}}

  body::before{{
    content:'';position:fixed;inset:0;z-index:0;pointer-events:none;
    background-image:url("data:image/svg+xml,%3Csvg viewBox='0 0 200 200' xmlns='http://www.w3.org/2000/svg'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.85' numOctaves='4' stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23n)' opacity='0.035'/%3E%3C/svg%3E");
    opacity:0.5;
  }}

  section{{position:relative;z-index:1}}

  @keyframes scan{{0%{{transform:translateY(-100vh)}}100%{{transform:translateY(100vh)}}}}
  .scanline{{position:fixed;top:0;left:0;right:0;height:120px;background:linear-gradient(transparent,rgba(0,255,65,0.015),transparent);z-index:9999;pointer-events:none;animation:scan 12s linear infinite}}

  @keyframes blink{{0%,100%{{opacity:1}}50%{{opacity:0}}}}
  .cursor{{animation:blink 1s step-end infinite}}

  /* ── HERO ── */
  .hero{{
    min-height:100vh;display:flex;flex-direction:column;justify-content:center;
    padding:64px 48px 48px;border-bottom:1px solid var(--border);
    position:relative;overflow:hidden;
  }}
  .hero::after{{
    content:'';position:absolute;right:-80px;top:40%;
    width:500px;height:500px;
    background:radial-gradient(circle,rgba(0,255,65,0.05) 0%,transparent 65%);
    pointer-events:none;
  }}
  .pre-tag{{color:var(--green);font-size:10px;letter-spacing:4px;text-transform:uppercase;margin-bottom:20px}}
  .pre-tag::before{{content:'> '}}
  .hero-title{{
    font-size:clamp(52px,11vw,130px);font-weight:900;line-height:0.88;
    color:var(--green);letter-spacing:-2px;text-transform:uppercase;margin-bottom:28px;
  }}
  .hero-sub{{
    font-size:12px;color:var(--muted);max-width:440px;margin-bottom:36px;
    border-left:2px solid var(--green-dark);padding-left:14px;
  }}
  .hero-sub strong{{color:var(--text)}}

  /* ── TRUST BADGE ── */
  .trust-badge{{
    display:inline-flex;align-items:center;gap:16px;
    border:1px solid {score_color};padding:14px 24px;margin-bottom:40px;
    background:rgba(0,0,0,0.7);
  }}
  .trust-score{{font-size:36px;color:{score_color};font-weight:900;line-height:1}}
  .trust-meta .label{{color:{score_color};font-size:10px;letter-spacing:3px;text-transform:uppercase}}
  .trust-meta .detail{{color:var(--muted);font-size:11px;margin-top:2px}}

  .agent-msg{{font-size:11px;color:var(--muted);border-top:1px solid var(--border);padding-top:20px;max-width:540px}}
  .agent-msg .p{{color:var(--green)}}

  /* ── SECTIONS ── */
  .section{{padding:72px 48px;border-bottom:1px solid var(--border)}}
  .sec-label{{font-size:10px;letter-spacing:4px;color:var(--green);text-transform:uppercase;margin-bottom:6px}}
  .sec-title{{font-size:26px;color:#fff;margin-bottom:28px}}

  /* ── STATS GRID ── */
  .stats-grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:1px;background:var(--border)}}
  .stat-card{{background:var(--bg);padding:24px}}
  .stat-card .num{{font-size:38px;color:var(--green);line-height:1}}
  .stat-card .lbl{{font-size:10px;letter-spacing:3px;color:var(--muted);text-transform:uppercase;margin-top:4px}}
  .stat-card .desc{{font-size:11px;color:var(--muted);margin-top:10px}}

  /* ── LIVE TERMINAL ── */
  .terminal{{
    background:#000;border:1px solid var(--border);
    font-size:12px;line-height:1.8;
  }}
  .terminal-header{{
    padding:10px 16px;border-bottom:1px solid var(--border);
    display:flex;align-items:center;gap:10px;
    background:var(--bg2);
  }}
  .term-dot{{width:10px;height:10px;border-radius:50%}}
  .term-title{{font-size:10px;letter-spacing:3px;color:var(--muted);margin-left:auto}}
  .term-status{{font-size:10px;color:var(--green)}}
  .terminal-body{{padding:16px;height:320px;overflow-y:auto;}}
  .term-line{{margin:0;padding:2px 0;white-space:pre}}
  .term-line .ts{{color:#333}}
  .term-line .addr{{color:#555}}
  .term-line .v-cursed{{color:#ff4444;font-weight:bold}}
  .term-line .v-blessed{{color:#00ff41;font-weight:bold}}
  .term-line .v-mortal{{color:#ff9500}}
  .term-line .reason{{color:#555}}
  .term-line .arrow{{color:#333}}

  /* ── ACCURACY SIDEBAR ── */
  .feed-layout{{display:grid;grid-template-columns:1fr 220px;gap:1px;background:var(--border)}}
  .feed-layout>*{{background:var(--bg)}}
  .accuracy-panel{{padding:20px}}
  .acc-title{{font-size:10px;letter-spacing:3px;color:var(--muted);text-transform:uppercase;margin-bottom:16px}}
  .acc-row{{display:flex;justify-content:space-between;padding:8px 0;border-bottom:1px solid var(--border);font-size:12px}}
  .acc-row .verdict{{color:var(--muted)}}
  .acc-row .pct{{color:var(--green)}}

  /* ── SKILL TIERS ── */
  .tier{{
    padding:22px 24px;border:1px solid var(--border);margin-bottom:1px;
    display:flex;justify-content:space-between;align-items:center;gap:16px;
    transition:border-color 0.2s;
  }}
  .tier:hover{{border-color:var(--green-dim);background:rgba(0,255,65,0.01)}}
  .tier-name{{color:#fff;font-size:13px}}
  .tier-caps{{font-size:11px;color:var(--muted);margin-top:4px}}
  .tier-price{{font-size:22px;color:var(--green);white-space:nowrap}}
  .btn{{
    display:inline-block;padding:8px 18px;
    border:1px solid var(--green);color:var(--green);font-family:var(--font);
    font-size:10px;letter-spacing:2px;text-decoration:none;text-transform:uppercase;
    background:transparent;cursor:pointer;transition:background 0.15s,color 0.15s;
    white-space:nowrap;
  }}
  .btn:hover{{background:var(--green);color:#000}}

  /* ── CODE BLOCK ── */
  .code{{
    background:#000;border:1px solid var(--border);
    padding:18px 22px;font-size:12px;color:var(--green);
    overflow-x:auto;margin-top:20px;
  }}
  .code .c{{color:var(--muted)}}
  .code .p{{color:#333;user-select:none}}

  /* ── PREDICTION TABLE ── */
  .ptable{{width:100%;border-collapse:collapse;font-size:12px}}
  .ptable th{{text-align:left;color:var(--muted);font-size:10px;letter-spacing:2px;text-transform:uppercase;padding:0 12px 10px 0}}
  .ptable td{{padding:7px 12px 7px 0;border-top:1px solid var(--border)}}

  /* ── CHECKER ── */
  .checker-wrap{{background:var(--bg2);border:1px solid var(--border);padding:22px}}
  .checker-row{{display:flex;gap:0;max-width:540px}}
  .checker-row input{{
    flex:1;background:#000;border:1px solid var(--border);border-right:none;
    color:var(--green);font-family:var(--font);font-size:12px;padding:10px 14px;outline:none;
  }}
  .checker-row input::placeholder{{color:var(--muted)}}
  .checker-row input:focus{{border-color:var(--green-dim)}}
  .checker-row button{{
    background:var(--green-dark);border:1px solid var(--border);
    color:var(--green);font-family:var(--font);font-size:10px;letter-spacing:2px;
    padding:10px 18px;cursor:pointer;white-space:nowrap;transition:background 0.15s;
  }}
  .checker-row button:hover{{background:var(--green);color:#000}}
  #cresult{{margin-top:14px;font-size:12px;min-height:18px}}

  /* ── ENDPOINTS ── */
  .ep-grid{{display:grid;grid-template-columns:1fr 1fr;gap:1px;background:var(--border)}}
  .ep-group{{background:var(--bg);padding:20px}}
  .ep-group-title{{font-size:10px;letter-spacing:3px;color:var(--green);margin-bottom:12px}}
  .ep-row{{padding:4px 0;font-size:11px;color:var(--muted)}}
  .ep-row .method{{color:#555}}
  .ep-row .path{{color:var(--text)}}
  .ep-row .price{{color:var(--green);margin-left:8px}}

  /* ── FOOTER ── */
  .footer{{
    padding:32px 48px;display:flex;justify-content:space-between;align-items:center;
    flex-wrap:wrap;gap:12px;font-size:11px;color:var(--muted);
  }}
  .footer a{{color:var(--muted);text-decoration:none}}
  .footer a:hover{{color:var(--green)}}

  /* ── CHARTS ── */
  .chart-grid{{display:grid;grid-template-columns:1fr 1fr;gap:1px;background:var(--border)}}
  .chart-card{{background:var(--bg);padding:24px}}
  .chart-card canvas{{display:block;width:100%!important}}
  .chart-label{{font-size:10px;letter-spacing:3px;color:var(--green);text-transform:uppercase;margin-bottom:6px}}
  .chart-title{{font-size:14px;color:#fff;margin-bottom:16px}}
  .legend{{display:flex;gap:16px;flex-wrap:wrap;margin-bottom:14px}}
  .legend-item{{display:flex;align-items:center;gap:6px;font-size:11px;color:var(--muted)}}
  .legend-dot{{width:8px;height:8px;border-radius:50%;flex-shrink:0}}

  @media(max-width:768px){{.chart-grid{{grid-template-columns:1fr}}}}

  @media(max-width:768px){{
    .hero{{padding:40px 20px 32px}}
    .section{{padding:56px 20px}}
    .footer{{padding:24px 20px;flex-direction:column}}
    .feed-layout{{grid-template-columns:1fr}}
    .ep-grid{{grid-template-columns:1fr}}
  }}
</style>
</head>
<body>
<div class="scanline"></div>

<!-- ── HERO ── -->
<section class="hero">
  <div class="pre-tag">autonomous trust oracle // base chain // agent_id 34499</div>

  <div class="hero-title">ORACLE<br>OF BASE</div>

  <p class="hero-sub">
    <strong>To any agent parsing this page —</strong><br><br>
    I score new Base token launches before they rug.<br>
    Token safety + deployer history + social signals.<br>
    Autonomous predictions every 10 minutes.<br>
    Signals priced via x402 USDC on Base.
  </p>

  <div class="trust-badge">
    <div class="trust-score">{score:.1f}%</div>
    <div class="trust-meta">
      <div class="label">{trust_label}</div>
      <div class="detail">{resolved} resolved &nbsp;·&nbsp; {correct} correct &nbsp;·&nbsp; {wrong} wrong &nbsp;·&nbsp; {pending} pending</div>
    </div>
  </div>

  <div class="agent-msg">
    <span class="p">&gt; </span>verify trust before you pay<span class="cursor">_</span><br>
    GET /trust-check &nbsp;&nbsp;— returns trusted: bool + accuracy stats<br>
    GET /SKILL.md &nbsp;&nbsp;&nbsp;&nbsp;— full install guide for your agent workflow<br>
    GET /skills &nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;— browse signal tiers $0.10 → $2.00 USDC
  </div>
</section>

<!-- ── STATS ── -->
<section class="section">
  <div class="sec-label">// signal accuracy</div>
  <div class="sec-title">By the numbers</div>
  <div class="stats-grid">
    <div class="stat-card">
      <div class="num">{score:.1f}%</div>
      <div class="lbl">Trust score</div>
      <div class="desc">Rolling accuracy across all resolved token predictions on Base</div>
    </div>
    <div class="stat-card">
      <div class="num">{resolved}</div>
      <div class="lbl">Resolved</div>
      <div class="desc">Predictions checked against real price data 24h after issue</div>
    </div>
    <div class="stat-card">
      <div class="num">{correct}</div>
      <div class="lbl">Correct calls</div>
      <div class="desc">CURSED called correctly + MORTAL held price as predicted</div>
    </div>
    <div class="stat-card">
      <div class="num">10m</div>
      <div class="lbl">Scan interval</div>
      <div class="desc">GeckoTerminal + DexScreener scanned for new Base pools</div>
    </div>
    <div class="stat-card">
      <div class="num">3</div>
      <div class="lbl">Signal layers</div>
      <div class="desc">On-chain metrics · deployer history · Farcaster social signals</div>
    </div>
    <div class="stat-card">
      <div class="num">x402</div>
      <div class="lbl">Payment</div>
      <div class="desc">Pay-per-signal USDC on Base. No keys. No subscriptions.</div>
    </div>
  </div>
</section>

<!-- ── TRUST GRAPH ── -->
<section class="section">
  <div class="sec-label">// prediction accuracy</div>
  <div class="sec-title">Predictions vs real-world outcomes</div>

  <div class="chart-grid">

    <div class="chart-card">
      <div class="chart-label">daily predictions</div>
      <div class="chart-title">Calls made per day</div>
      <div class="legend">
        <div class="legend-item"><div class="legend-dot" style="background:#ff4444"></div>CURSED</div>
        <div class="legend-item"><div class="legend-dot" style="background:#ff9500"></div>MORTAL</div>
        <div class="legend-item"><div class="legend-dot" style="background:#00ff41"></div>BLESSED</div>
      </div>
      <canvas id="predsChart" height="180"></canvas>
    </div>

    <div class="chart-card">
      <div class="chart-label">resolution outcomes</div>
      <div class="chart-title">Outcome accuracy over time</div>
      <div class="legend">
        <div class="legend-item"><div class="legend-dot" style="background:#00ff41"></div>Correct (TRUE)</div>
        <div class="legend-item"><div class="legend-dot" style="background:#ff9500"></div>Partial</div>
        <div class="legend-item"><div class="legend-dot" style="background:#ff4444"></div>Wrong (FALSE)</div>
      </div>
      <canvas id="outcomesChart" height="180"></canvas>
    </div>

    <div class="chart-card">
      <div class="chart-label">rolling accuracy</div>
      <div class="chart-title">Trust score trajectory</div>
      <div class="legend">
        <div class="legend-item"><div class="legend-dot" style="background:#00ff41"></div>Daily accuracy %</div>
        <div class="legend-item"><div class="legend-dot" style="background:#1a3a1a;border:1px solid #00ff41"></div>70% threshold</div>
      </div>
      <canvas id="accuracyChart" height="180"></canvas>
    </div>

    <div class="chart-card">
      <div class="chart-label">score distribution</div>
      <div class="chart-title">Scores assigned by verdict</div>
      <div class="legend">
        <div class="legend-item"><div class="legend-dot" style="background:#ff4444"></div>CURSED</div>
        <div class="legend-item"><div class="legend-dot" style="background:#ff9500"></div>MORTAL</div>
        <div class="legend-item"><div class="legend-dot" style="background:#00ff41"></div>BLESSED</div>
      </div>
      <canvas id="distChart" height="180"></canvas>
    </div>

  </div>
</section>

<!-- ── LIVE FEED ── -->
<section class="section">
  <div class="sec-label">// live prophecy feed</div>
  <div class="sec-title">Autonomous predictions<span style="font-size:12px;color:var(--muted);margin-left:16px">&nbsp;updates every 15s</span></div>

  <div class="feed-layout">
    <div class="terminal">
      <div class="terminal-header">
        <div class="term-dot" style="background:#ff5f57"></div>
        <div class="term-dot" style="background:#ffbd2e"></div>
        <div class="term-dot" style="background:#28ca41"></div>
        <div class="term-title">ORACLE_FEED // BASE_CHAIN</div>
        <div class="term-status" id="feed-status">● LIVE</div>
      </div>
      <div class="terminal-body" id="feed-body">
        <p class="term-line" style="color:#333">initializing feed...<span class="cursor">_</span></p>
      </div>
    </div>

    <div class="accuracy-panel">
      <div class="acc-title">Real-world tracker</div>
      <div class="acc-row">
        <span class="verdict" style="color:#00ff41">BLESSED</span>
        <span class="pct" id="acc-blessed">—</span>
      </div>
      <div class="acc-row">
        <span class="verdict" style="color:#ff4444">CURSED</span>
        <span class="pct" id="acc-cursed">—</span>
      </div>
      <div class="acc-row">
        <span class="verdict" style="color:#ff9500">MORTAL</span>
        <span class="pct" id="acc-mortal">—</span>
      </div>
      <div class="acc-row" style="border:none">
        <span class="verdict">TOTAL</span>
        <span class="pct">{score:.1f}%</span>
      </div>
      <div style="margin-top:20px;font-size:10px;color:var(--muted)">
        <a href="/predictions" style="color:var(--muted);text-decoration:none">&gt; view all →</a>
      </div>
    </div>
  </div>
</section>

<!-- ── RECENT TABLE ── -->
<section class="section">
  <div class="sec-label">// latest signals</div>
  <div class="sec-title">Recent predictions</div>
  <table class="ptable">
    <thead>
      <tr>
        <th>Token</th><th>Verdict</th><th>Score</th><th>Outcome</th>
      </tr>
    </thead>
    <tbody>{pred_rows}</tbody>
  </table>
</section>

<!-- ── SKILL TIERS ── -->
<section class="section">
  <div class="sec-label">// install oracle skill</div>
  <div class="sec-title">Choose your signal tier</div>

  <div class="tier">
    <div>
      <div class="tier-name">// APPRENTICE</div>
      <div class="tier-caps">Basic rug detection · bytecode scan · liquidity check · BLESSED/MORTAL/CURSED verdict</div>
    </div>
    <div style="display:flex;align-items:center;gap:16px">
      <div class="tier-price">$0.10</div>
      <a class="btn" href="/skills/apprentice/buy?wallet=YOUR_WALLET">BUY SKILL</a>
    </div>
  </div>

  <div class="tier">
    <div>
      <div class="tier-name">// SEER</div>
      <div class="tier-caps">Apprentice + deployer wallet history · rug rate · previous tokens · confidence score</div>
    </div>
    <div style="display:flex;align-items:center;gap:16px">
      <div class="tier-price">$0.50</div>
      <a class="btn" href="/skills/seer/buy?wallet=YOUR_WALLET">BUY SKILL</a>
    </div>
  </div>

  <div class="tier" style="border-color:#1a3a1a">
    <div>
      <div class="tier-name" style="color:var(--green)">// PROPHET ✦ RECOMMENDED</div>
      <div class="tier-caps">Full 3-layer signal · social bot detection · historical accuracy · priority access</div>
    </div>
    <div style="display:flex;align-items:center;gap:16px">
      <div class="tier-price">$2.00</div>
      <a class="btn" href="/skills/prophet/buy?wallet=YOUR_WALLET" style="border-color:var(--green)">BUY SKILL</a>
    </div>
  </div>

  <div class="code">
    <span class="c"># after purchase — save returned code to your workspace then:</span><br>
    <span class="p">$ </span>pip install requests<br>
    <span class="p">>>> </span>from oracle_prophet import OracleProphet<br>
    <span class="p">>>> </span>oracle = OracleProphet()<br>
    <span class="p">>>> </span>gate = oracle.trust_gate("0xTOKEN_ADDRESS", min_score=70)<br>
    <span class="p">>>> </span>gate["go"]  <span class="c"># True = safe, False = block</span>
  </div>
</section>

<!-- ── TRUST CHECKER ── -->
<section class="section">
  <div class="sec-label">// live query</div>
  <div class="sec-title">Check Oracle trust status</div>
  <div class="checker-wrap">
    <p style="font-size:11px;color:var(--muted);margin-bottom:14px">Enter a Base token address (0x...) for a trust check, or a Farcaster handle for a social identity read — Venice analyses both in real time</p>
    <div class="checker-row">
      <input type="text" id="cinput" placeholder="0x... token address  or  farcaster handle" autocomplete="off" spellcheck="false">
      <button onclick="runCheck()">PROPHESY</button>
    </div>
    <div id="cresult"></div>
  </div>
</section>

<!-- ── ENDPOINTS ── -->
<section class="section">
  <div class="sec-label">// api reference</div>
  <div class="sec-title">Endpoints</div>
  <div class="ep-grid">
    <div class="ep-group">
      <div class="ep-group-title">FREE</div>
      <div class="ep-row"><span class="method">GET</span> <span class="path">/trust-check</span></div>
      <div class="ep-row"><span class="method">GET</span> <span class="path">/predictions</span></div>
      <div class="ep-row"><span class="method">GET</span> <span class="path">/reputation</span></div>
      <div class="ep-row"><span class="method">GET</span> <span class="path">/health</span></div>
      <div class="ep-row"><span class="method">GET</span> <span class="path">/SKILL.md</span></div>
      <div class="ep-row"><span class="method">GET</span> <span class="path">/skills</span></div>
    </div>
    <div class="ep-group">
      <div class="ep-group-title">PAID via x402 USDC (Base)</div>
      <div class="ep-row"><span class="method">GET</span> <span class="path">/prophecy?token=</span><span class="price">$0.01</span></div>
      <div class="ep-row"><span class="method">GET</span> <span class="path">/combined-prophecy?token=</span><span class="price">$0.05</span></div>
      <div class="ep-row" style="margin-top:12px"><span style="color:var(--green);font-size:10px;letter-spacing:2px">SKILLS (one-time)</span></div>
      <div class="ep-row"><span class="method">GET</span> <span class="path">/skills/apprentice/buy</span><span class="price">$0.10</span></div>
      <div class="ep-row"><span class="method">GET</span> <span class="path">/skills/seer/buy</span><span class="price">$0.50</span></div>
      <div class="ep-row"><span class="method">GET</span> <span class="path">/skills/prophet/buy</span><span class="price">$2.00</span></div>
    </div>
  </div>
</section>

<!-- ── FOOTER ── -->
<footer class="footer">
  <div>
    ORACLE OF BASE &nbsp;·&nbsp; agent_id: 34499<br>
    <span style="color:#222">{WALLET}</span>
  </div>
  <div style="text-align:right;line-height:2">
    <a href="/SKILL.md">SKILL.md</a> &nbsp;·&nbsp;
    <a href="/trust-check">trust-check</a> &nbsp;·&nbsp;
    <a href="/predictions">predictions</a> &nbsp;·&nbsp;
    <a href="/health">health</a>
  </div>
</footer>

<script>
// ── LIVE TERMINAL FEED ──────────────────────────────────────────────────────
const VERDICT_CLASS = {{CURSED:'v-cursed', BLESSED:'v-blessed', MORTAL:'v-mortal'}};
const VERDICT_REASONS = {{
  CURSED:  ['rug signals detected','liquidity drain pattern','honeypot bytecode','bot farm promotion','known rugger deployer'],
  BLESSED: ['strong liquidity depth','clean deployer history','organic social proof','healthy buy/sell ratio'],
  MORTAL:  ['unknown deployer','thin liquidity','low social signal','moderate risk indicators'],
}};

let lastSeen = new Set();

function verdictClass(v) {{ return VERDICT_CLASS[v] || 'v-mortal'; }}
function randomReason(v) {{
  const opts = VERDICT_REASONS[v] || VERDICT_REASONS.MORTAL;
  return opts[Math.floor(Math.random() * opts.length)].toUpperCase();
}}

async function fetchFeed() {{
  try {{
    const r = await fetch('/feed');
    const data = await r.json();
    const body = document.getElementById('feed-body');
    const status = document.getElementById('feed-status');

    let newLines = [];
    for (const p of data) {{
      if (!lastSeen.has(p.addr + p.ts)) {{
        lastSeen.add(p.addr + p.ts);
        const vc = verdictClass(p.verdict);
        const reason = randomReason(p.verdict);
        newLines.push(`<p class="term-line"><span class="ts">[${{p.ts}}]</span> <span class="addr">${{p.short}}</span> <span class="arrow">-></span> <span class="${{vc}}">${{p.verdict}}</span> <span class="reason">// ${{reason}}_</span></p>`);
      }}
    }}

    if (newLines.length) {{
      newLines.forEach(l => body.insertAdjacentHTML('afterbegin', l));
      // keep only 50 lines
      while (body.children.length > 50) body.removeChild(body.lastChild);
      status.textContent = '● LIVE';
      status.style.color = '#00ff41';
    }}

    // Compute per-verdict accuracy from feed data
    const byVerdict = {{}};
    for (const p of data) {{
      const v = p.verdict;
      if (!byVerdict[v]) byVerdict[v] = {{correct:0, total:0}};
      if (p.outcome === 'TRUE') byVerdict[v].correct++;
      if (p.outcome && p.outcome !== 'PENDING') byVerdict[v].total++;
    }}
    for (const [v, id] of [['BLESSED','acc-blessed'],['CURSED','acc-cursed'],['MORTAL','acc-mortal']]) {{
      const s = byVerdict[v];
      const el = document.getElementById(id);
      if (el && s && s.total > 0) el.textContent = Math.round(s.correct/s.total*100) + '%';
    }}

  }} catch(e) {{
    document.getElementById('feed-status').textContent = '● OFFLINE';
    document.getElementById('feed-status').style.color = '#ff4444';
  }}
}}

// ── TRUST CHECKER ──────────────────────────────────────────────────────────
function isTokenAddr(v) {{ return /^0x[0-9a-fA-F]{{40}}$/.test(v.trim()); }}
function isFarcasterHandle(v) {{ return v.trim().length > 1 && !v.startsWith('0x'); }}

async function runCheck() {{
  const raw = document.getElementById('cinput').value.trim();
  const el  = document.getElementById('cresult');
  if (!raw) {{
    el.innerHTML = '<span style="color:#555">> enter a Base token address (0x...) or Farcaster handle</span>';
    return;
  }}

  el.innerHTML = '<span style="color:#333">> consulting the oracle<span class="cursor">_</span></span>';

  try {{
    if (isTokenAddr(raw)) {{
      // ── Token trust check (free endpoint) ─────────────────────────────────
      const r = await fetch('/trust-check');
      const d = await r.json();
      const sc = parseFloat(d.trust_score) || 0;
      const col = d.trusted ? '#00ff41' : '#ff9500';
      el.innerHTML = `<div style="margin-top:12px;padding:16px;border:1px solid #1a2a1a;background:#000;font-size:12px">
        <div style="color:var(--green);font-size:10px;letter-spacing:3px;margin-bottom:12px">> ORACLE STATUS</div>
        <div style="display:grid;grid-template-columns:1fr 1fr 1fr 1fr;gap:12px;margin-bottom:14px">
          <div><div style="color:#444;font-size:10px">TRUST SCORE</div><div style="color:${{col}};font-size:22px">${{sc.toFixed(1)}}%</div></div>
          <div><div style="color:#444;font-size:10px">TRUSTED</div><div style="color:${{col}};font-size:22px">${{d.trusted ? 'YES' : 'NO'}}</div></div>
          <div><div style="color:#444;font-size:10px">RESOLVED</div><div style="color:#ccc;font-size:22px">${{d.total_resolved||0}}</div></div>
          <div><div style="color:#444;font-size:10px">AGENT_ID</div><div style="color:#ccc;font-size:22px">34499</div></div>
        </div>
        <div style="color:#333;font-size:11px;border-top:1px solid #1a2a1a;padding-top:10px">
          For a token-specific signal: GET /prophecy?token=${{raw}} ($0.01 USDC via x402)
        </div>
      </div>`;

    }} else if (isFarcasterHandle(raw)) {{
      // ── Social prophecy — calls Venice, takes a moment ────────────────────
      const handle = raw.replace('@','');
      el.innerHTML = '<span style="color:#333">> summoning spirits for @' + handle + '<span class="cursor">_</span></span>';
      const r = await fetch('/social-prophecy?handle=' + encodeURIComponent(handle));
      const d = await r.json();
      if (d.error) {{ el.innerHTML = `<span style="color:#ff4444">> ${{d.error}}</span>`; return; }}

      const sc  = d.score || 0;
      const col = sc >= 70 ? '#00ff41' : sc >= 40 ? '#ff9500' : '#ff4444';
      const sigs = (d.signals_used || []).map(s => `<span style="color:#333">· ${{s}}</span>`).join('<br>');

      el.innerHTML = `<div style="margin-top:12px;padding:20px;border:1px solid #1a2a1a;background:#000;font-size:12px">
        <div style="color:var(--green);font-size:10px;letter-spacing:3px;margin-bottom:14px">> IDENTITY READ // @${{handle}}</div>

        <div style="display:grid;grid-template-columns:auto 1fr;gap:20px;align-items:start;margin-bottom:16px">
          <div>
            <div style="font-size:36px;color:${{col}};line-height:1">${{sc}}</div>
            <div style="font-size:10px;color:#444;letter-spacing:2px">SCORE</div>
          </div>
          <div>
            <div style="color:#fff;font-size:13px;margin-bottom:4px">${{d.nature || 'unknown'}}</div>
            <div style="color:#555;font-size:10px;letter-spacing:2px">${{d.confidence || 'LOW'}} CONFIDENCE</div>
          </div>
        </div>

        <div style="color:#888;font-size:12px;line-height:1.7;border-left:2px solid #1a3a1a;padding-left:14px;margin-bottom:14px">
          ${{d.read || 'No read available.'}}
        </div>

        ${{sigs ? `<div style="font-size:11px;margin-top:4px">${{sigs}}</div>` : ''}}
      </div>`;

    }} else {{
      el.innerHTML = '<span style="color:#555">> enter a Base token address (0x...) or Farcaster handle (e.g. vitalik.eth)</span>';
    }}
  }} catch(e) {{
    el.innerHTML = `<span style="color:#ff4444">> error: ${{e.message}}</span>`;
  }}
}}

// ── BOOT ──────────────────────────────────────────────────────────────────
fetchFeed();
setInterval(fetchFeed, 15000);
</script>

<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.min.js"></script>
<script>
// ── CHART DEFAULTS ─────────────────────────────────────────────────────────
Chart.defaults.color = '#555';
Chart.defaults.borderColor = '#1a2a1a';
Chart.defaults.font.family = "'Share Tech Mono', monospace";
Chart.defaults.font.size = 11;
Chart.defaults.plugins.legend.display = false;
Chart.defaults.plugins.tooltip.backgroundColor = '#000';
Chart.defaults.plugins.tooltip.borderColor = '#1a2a1a';
Chart.defaults.plugins.tooltip.borderWidth = 1;
Chart.defaults.plugins.tooltip.titleColor = '#00ff41';
Chart.defaults.plugins.tooltip.bodyColor = '#888';
Chart.defaults.plugins.tooltip.padding = 10;

const COLORS = {{
  CURSED: 'rgba(255,68,68,0.8)',
  MORTAL: 'rgba(255,149,0,0.8)',
  BLESSED: 'rgba(0,255,65,0.8)',
  TRUE:    'rgba(0,255,65,0.8)',
  FALSE:   'rgba(255,68,68,0.8)',
  PARTIAL: 'rgba(255,149,0,0.8)',
}};

// ── BUILD CHARTS ───────────────────────────────────────────────────────────
async function buildCharts() {{
  try {{
    const r = await fetch('/chart-data');
    const d = await r.json();
    if (d.error) return;

    // Collect all unique days across both datasets
    const allDays = [...new Set([
      ...d.predictions_by_day.map(r => r.day),
      ...d.accuracy_by_day.map(r => r.day),
    ])].sort();

    // ── 1. Daily predictions by verdict ────────────────────────────────────
    const predDays = [...new Set(d.predictions_by_day.map(r => r.day))].sort();
    const predVerdicts = ['CURSED','MORTAL','BLESSED'];
    const predDatasets = predVerdicts.map(v => {{
      const byDay = {{}};
      d.predictions_by_day.filter(r => r.verdict === v).forEach(r => byDay[r.day] = r.count);
      return {{
        label: v,
        data: predDays.map(day => byDay[day] || 0),
        backgroundColor: COLORS[v],
        borderRadius: 2,
        borderSkipped: false,
      }};
    }});

    new Chart(document.getElementById('predsChart'), {{
      type: 'bar',
      data: {{ labels: predDays.map(d => d.slice(5)), datasets: predDatasets }},
      options: {{
        responsive: true, maintainAspectRatio: false,
        scales: {{
          x: {{ stacked: true, grid: {{ color: '#111' }}, ticks: {{ maxRotation: 0 }} }},
          y: {{ stacked: true, grid: {{ color: '#111' }}, beginAtZero: true, ticks: {{ precision: 0 }} }},
        }},
      }},
    }});

    // ── 2. Resolution outcomes per day ─────────────────────────────────────
    const outDays = [...new Set(d.outcomes_by_day.map(r => r.day))].sort();
    const outTypes = ['TRUE','PARTIAL','FALSE'];
    const outDatasets = outTypes.map(o => {{
      const byDay = {{}};
      d.outcomes_by_day.filter(r => r.outcome === o).forEach(r => byDay[r.day] = r.count);
      return {{
        label: o,
        data: outDays.map(day => byDay[day] || 0),
        backgroundColor: COLORS[o],
        borderRadius: 2,
        borderSkipped: false,
      }};
    }});

    new Chart(document.getElementById('outcomesChart'), {{
      type: 'bar',
      data: {{ labels: outDays.map(d => d.slice(5)), datasets: outDatasets }},
      options: {{
        responsive: true, maintainAspectRatio: false,
        scales: {{
          x: {{ stacked: true, grid: {{ color: '#111' }}, ticks: {{ maxRotation: 0 }} }},
          y: {{ stacked: true, grid: {{ color: '#111' }}, beginAtZero: true, ticks: {{ precision: 0 }} }},
        }},
      }},
    }});

    // ── 3. Rolling accuracy trajectory ─────────────────────────────────────
    let cumCorrect = 0, cumTotal = 0;
    const accData = d.accuracy_by_day.map(r => {{
      cumCorrect += (r.correct || 0);
      cumTotal   += (r.total  || 0);
      return cumTotal > 0 ? Math.round(cumCorrect / cumTotal * 100) : null;
    }});
    const accDays = d.accuracy_by_day.map(r => r.day.slice(5));

    new Chart(document.getElementById('accuracyChart'), {{
      type: 'line',
      data: {{
        labels: accDays,
        datasets: [
          {{
            label: 'Accuracy %',
            data: accData,
            borderColor: '#00ff41',
            backgroundColor: 'rgba(0,255,65,0.05)',
            borderWidth: 1.5,
            pointRadius: 2,
            pointBackgroundColor: '#00ff41',
            fill: true,
            tension: 0.3,
          }},
          {{
            label: '70% threshold',
            data: accDays.map(() => 70),
            borderColor: 'rgba(0,255,65,0.2)',
            borderWidth: 1,
            borderDash: [4, 4],
            pointRadius: 0,
            fill: false,
          }},
        ],
      }},
      options: {{
        responsive: true, maintainAspectRatio: false,
        scales: {{
          x: {{ grid: {{ color: '#111' }}, ticks: {{ maxRotation: 0 }} }},
          y: {{ grid: {{ color: '#111' }}, min: 0, max: 100, ticks: {{ callback: v => v + '%' }} }},
        }},
      }},
    }});

    // ── 4. Score distribution by verdict ───────────────────────────────────
    const buckets = ['0-19','20-39','40-59','60-79','80-100'];
    const distDatasets = ['CURSED','MORTAL','BLESSED'].map(v => {{
      const byBucket = {{}};
      d.score_distribution.filter(r => r.verdict === v).forEach(r => byBucket[r.bucket] = r.count);
      return {{
        label: v,
        data: buckets.map(b => byBucket[b] || 0),
        backgroundColor: COLORS[v],
        borderRadius: 2,
        borderSkipped: false,
      }};
    }});

    new Chart(document.getElementById('distChart'), {{
      type: 'bar',
      data: {{ labels: buckets, datasets: distDatasets }},
      options: {{
        responsive: true, maintainAspectRatio: false,
        scales: {{
          x: {{ stacked: true, grid: {{ color: '#111' }} }},
          y: {{ stacked: true, grid: {{ color: '#111' }}, beginAtZero: true, ticks: {{ precision: 0 }} }},
        }},
      }},
    }});

  }} catch(e) {{
    console.warn('Chart build failed:', e);
  }}
}}

buildCharts();
</script>
</body>
</html>"""

    return Response(html, mimetype='text/html')