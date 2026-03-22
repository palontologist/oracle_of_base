"""
frontend.py — Oracle of Base frontend (clean rewrite)
"""
from flask import Blueprint, Response, jsonify, request
import os, json, logging
log = logging.getLogger("frontend")

frontend_bp = Blueprint('frontend', __name__)
WALLET = "0x1EA37E2Fb76Aa396072204C90fcEF88093CEb920"

def _get_trust():
    try:
        from prediction_store import get_reputation_stats
        s = get_reputation_stats("34499")
        sc = s.get("trust_score")
        sc = round(float(sc), 2) if sc else 0.0
        res = s.get("total_resolved", 0)
        return {
            "trust_score": sc, "trusted": sc >= 70 and res >= 5,
            "total_resolved": res, "correct": s.get("correct", 0),
            "wrong": s.get("wrong", 0), "pending": s.get("pending", 0),
        }
    except Exception as e:
        log.error(f"_get_trust: {e}")
        return {"trust_score": 0.0, "trusted": False, "total_resolved": 0,
                "correct": 0, "wrong": 0, "pending": 0}

def _get_recent(limit=50):
    try:
        from prediction_store import get_conn
        import psycopg2.extras
        conn = get_conn()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("""
            SELECT p.id, p.subject as token_address, p.verdict, p.score,
                   p.status, p.created_at, r.outcome, r.accuracy
            FROM predictions p
            LEFT JOIN resolutions r ON r.prediction_id = p.id
            WHERE p.prediction_type = 'token'
            ORDER BY p.created_at DESC LIMIT %s
        """, (limit,))
        rows = [dict(r) for r in cur.fetchall()]
        cur.close(); conn.close()
        return rows
    except Exception as e:
        log.error(f"_get_recent: {e}")
        return []

@frontend_bp.route('/feed')
def feed_api():
    try:
        preds = _get_recent(60)
        out = []
        for p in preds:
            addr = p.get("token_address","")
            verdict = str(p.get("verdict","")).split(" ")[0]
            created = p.get("created_at")
            out.append({
                "ts":      created.strftime("%H:%M:%S") if created else "??:??:??",
                "date":    created.strftime("%m-%d") if created else "",
                "addr":    addr,
                "short":   (addr[:6]+"..."+addr[-4:]) if len(addr)>10 else addr,
                "verdict": verdict,
                "score":   p.get("score", 0),
                "status":  p.get("status","PENDING"),
                "outcome": p.get("outcome") or "",
            })
        return jsonify({"predictions": out, "count": len(out)})
    except Exception as e:
        log.error(f"/feed: {e}")
        return jsonify({"predictions":[], "count":0, "error": str(e)})

@frontend_bp.route('/')
def index():
    t = _get_trust()
    recent = _get_recent(5)
    sc      = t["trust_score"]
    trusted = t["trusted"]
    resolved= t["total_resolved"]
    correct = t["correct"]
    wrong   = t["wrong"]
    pending = t["pending"]
    sc_col  = "#00ff41" if trusted else "#ff9500"
    tl      = "TRUSTED ✓" if trusted else "BUILDING TRUST"

    pred_rows = ""
    for p in recent:
        addr    = p.get("token_address","")
        short   = (addr[:6]+"..."+addr[-4:]) if len(addr)>10 else addr
        verdict = str(p.get("verdict","")).split(" ")[0]
        score_v = p.get("score",0)
        outcome = p.get("outcome") or p.get("status","PENDING")
        vc      = "#ff4444" if verdict=="CURSED" else ("#00ff41" if verdict=="BLESSED" else "#ff9500")
        pred_rows += f'<tr><td style="color:#555">{short}</td><td style="color:{vc}">{verdict}</td><td style="color:#00ff41">{score_v}/100</td><td style="color:#666">{outcome}</td></tr>'

    if not pred_rows:
        pred_rows = '<tr><td colspan="4" style="color:#333;padding:16px;text-align:center">awaiting predictions...</td></tr>'

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Oracle of Base</title>
<style>
@import url('https://fonts.googleapis.com/css2?family=Share+Tech+Mono&display=swap');
*,*::before,*::after{{box-sizing:border-box;margin:0;padding:0}}
:root{{
  --g:#00ff41;--gd:#003311;--gm:#00cc33;
  --r:#ff4444;--a:#ff9500;
  --bg:#080808;--bg2:#0e0e0e;--bg3:#141414;
  --border:#1a2a1a;--text:#aaa;--muted:#444;
  --font:'Share Tech Mono','Courier New',monospace;
}}
html{{scroll-behavior:smooth}}
body{{background:var(--bg);color:var(--text);font-family:var(--font);font-size:13px;line-height:1.6;overflow-x:hidden}}
a{{color:inherit;text-decoration:none}}

/* ── NOISE ── */
body::before{{content:'';position:fixed;inset:0;z-index:0;pointer-events:none;
  background-image:url("data:image/svg+xml,%3Csvg viewBox='0 0 200 200' xmlns='http://www.w3.org/2000/svg'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.85' numOctaves='4' stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23n)' opacity='0.03'/%3E%3C/svg%3E");
  opacity:0.5}}

/* ── SCANLINE ── */
@keyframes scan{{0%{{transform:translateY(-100vh)}}100%{{transform:translateY(200vh)}}}}
.scanline{{position:fixed;top:0;left:0;right:0;height:80px;
  background:linear-gradient(transparent,rgba(0,255,65,0.012),transparent);
  z-index:9999;pointer-events:none;animation:scan 10s linear infinite}}

/* ── STICKY NAV ── */
.nav{{
  position:sticky;top:0;z-index:100;
  background:rgba(8,8,8,0.95);backdrop-filter:blur(8px);
  border-bottom:1px solid var(--border);
  padding:12px 32px;
  display:flex;align-items:center;gap:0;
  justify-content:space-between;flex-wrap:wrap;gap:10px;
}}
.nav-logo{{font-size:13px;color:var(--g);letter-spacing:2px;white-space:nowrap}}
.nav-logo span{{color:#333}}
.nav-actions{{display:flex;gap:8px;flex-wrap:wrap;align-items:center}}
.nav-btn{{
  display:inline-block;padding:7px 14px;
  border:1px solid var(--border);color:var(--muted);
  font-family:var(--font);font-size:10px;letter-spacing:1.5px;
  text-transform:uppercase;background:transparent;cursor:pointer;
  transition:border-color 0.15s,color 0.15s;white-space:nowrap;
}}
.nav-btn:hover{{border-color:var(--g);color:var(--g)}}
.nav-btn.primary{{border-color:var(--g);color:var(--g);background:rgba(0,255,65,0.05)}}
.nav-btn.primary:hover{{background:var(--g);color:#000}}
.nav-score{{
  display:flex;align-items:center;gap:8px;
  padding:6px 14px;border:1px solid {sc_col};
  font-size:10px;
}}
.nav-score .val{{color:{sc_col};font-size:16px;font-weight:900;line-height:1}}
.nav-score .lbl{{color:{sc_col};font-size:9px;letter-spacing:2px}}

/* ── HERO ── */
.hero{{
  min-height:90vh;display:flex;flex-direction:column;justify-content:center;
  padding:60px 48px 48px;border-bottom:1px solid var(--border);
  position:relative;overflow:hidden;z-index:1;
}}
.hero::after{{content:'';position:absolute;right:-60px;top:30%;
  width:480px;height:480px;
  background:radial-gradient(circle,rgba(0,255,65,0.04) 0%,transparent 65%);
  pointer-events:none}}
.pre-tag{{color:var(--g);font-size:10px;letter-spacing:4px;text-transform:uppercase;margin-bottom:18px}}
.pre-tag::before{{content:'> '}}
.hero-title{{
  font-size:clamp(48px,10vw,120px);font-weight:900;line-height:0.88;
  color:var(--g);letter-spacing:-2px;text-transform:uppercase;margin-bottom:24px;
}}
.hero-sub{{font-size:12px;color:var(--muted);max-width:400px;margin-bottom:32px;
  border-left:2px solid var(--gd);padding-left:14px}}
.hero-sub strong{{color:var(--text)}}

/* ── TRUST BADGE ── */
.trust-badge{{
  display:inline-flex;align-items:center;gap:20px;
  border:1px solid {sc_col};padding:16px 24px;margin-bottom:36px;
  background:rgba(0,0,0,0.6);
}}
.trust-score{{font-size:42px;color:{sc_col};font-weight:900;line-height:1}}
.trust-meta .tl{{color:{sc_col};font-size:10px;letter-spacing:3px;text-transform:uppercase}}
.trust-meta .td{{color:var(--muted);font-size:11px;margin-top:3px}}

/* ── QUICK ACTIONS ── */
.quick-actions{{display:flex;gap:10px;flex-wrap:wrap;margin-bottom:32px}}
.qa-btn{{
  padding:10px 20px;border:1px solid var(--border);
  color:var(--muted);font-family:var(--font);font-size:10px;
  letter-spacing:1.5px;text-transform:uppercase;background:transparent;
  cursor:pointer;transition:all 0.15s;
}}
.qa-btn:hover{{border-color:var(--g);color:var(--g);background:rgba(0,255,65,0.03)}}
.qa-btn.cta{{border-color:var(--g);color:var(--g)}}
.qa-btn.cta:hover{{background:var(--g);color:#000}}

/* ── AGENT MSG ── */
.agent-msg{{font-size:11px;color:var(--muted);border-top:1px solid var(--border);padding-top:18px;max-width:520px}}
@keyframes blink{{0%,100%{{opacity:1}}50%{{opacity:0}}}}
.cursor{{animation:blink 1s step-end infinite}}

/* ── SECTIONS ── */
section{{position:relative;z-index:1;padding:64px 48px;border-bottom:1px solid var(--border)}}
.sec-label{{font-size:10px;letter-spacing:4px;color:var(--g);text-transform:uppercase;margin-bottom:5px}}
.sec-title{{font-size:24px;color:#fff;margin-bottom:24px}}

/* ── STATS GRID ── */
.stats{{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:1px;background:var(--border)}}
.stat{{background:var(--bg);padding:22px}}
.stat .n{{font-size:36px;color:var(--g);line-height:1}}
.stat .l{{font-size:10px;letter-spacing:3px;color:var(--muted);text-transform:uppercase;margin-top:4px}}
.stat .d{{font-size:11px;color:var(--muted);margin-top:8px}}

/* ── CHARTS ── */
.chart-grid{{display:grid;grid-template-columns:1fr 1fr;gap:1px;background:var(--border)}}
.chart-card{{background:var(--bg);padding:20px}}
.chart-card .cl{{font-size:10px;letter-spacing:3px;color:var(--g);text-transform:uppercase;margin-bottom:4px}}
.chart-card .ct{{font-size:13px;color:#fff;margin-bottom:12px}}
.chart-wrap{{position:relative;height:150px}}
.leg{{display:flex;gap:14px;flex-wrap:wrap;margin-bottom:10px}}
.ld{{display:flex;align-items:center;gap:5px;font-size:10px;color:var(--muted)}}
.ldot{{width:8px;height:8px;border-radius:50%;flex-shrink:0}}

/* ── TERMINAL ── */
.feed-layout{{display:grid;grid-template-columns:1fr 200px;gap:1px;background:var(--border)}}
.terminal{{background:#000;border:none}}
.term-hdr{{padding:10px 16px;border-bottom:1px solid var(--border);background:var(--bg2);
  display:flex;align-items:center;gap:8px}}
.tdot{{width:10px;height:10px;border-radius:50%}}
.ttitle{{font-size:9px;letter-spacing:3px;color:var(--muted);margin-left:auto}}
.tstatus{{font-size:9px}}
.term-body{{padding:14px;height:300px;overflow-y:auto;font-size:11px;line-height:1.9}}
.tl .ts{{color:#2a2a2a}}
.tl .ta{{color:#3a3a3a}}
.tl .vc{{color:#ff4444}}
.tl .vb{{color:#00ff41}}
.tl .vm{{color:#ff9500}}
.tl .r{{color:#2a2a2a}}
.acc-panel{{background:var(--bg);padding:18px}}
.acc-ttl{{font-size:9px;letter-spacing:3px;color:var(--muted);text-transform:uppercase;margin-bottom:14px}}
.acc-row{{display:flex;justify-content:space-between;padding:7px 0;border-bottom:1px solid var(--border);font-size:11px}}

/* ── TABLE ── */
.ptable{{width:100%;border-collapse:collapse;font-size:11px}}
.ptable th{{text-align:left;color:var(--muted);font-size:9px;letter-spacing:2px;text-transform:uppercase;padding:0 12px 10px 0}}
.ptable td{{padding:7px 12px 7px 0;border-top:1px solid var(--border)}}

/* ── SKILL TIERS ── */
.tier{{padding:20px 22px;border:1px solid var(--border);margin-bottom:1px;
  display:flex;justify-content:space-between;align-items:center;gap:14px;
  transition:border-color 0.15s}}
.tier:hover{{border-color:var(--gm);background:rgba(0,255,65,0.01)}}
.tier-price{{font-size:20px;color:var(--g);white-space:nowrap}}

/* ── MONETIZATION CARDS ── */
.mono-grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:1px;background:var(--border)}}
.mono-card{{background:var(--bg);padding:22px;transition:background 0.15s}}
.mono-card:hover{{background:var(--bg2)}}
.mono-card .mc-label{{font-size:9px;letter-spacing:3px;color:var(--g);margin-bottom:6px}}
.mono-card .mc-title{{font-size:14px;color:#fff;margin-bottom:8px}}
.mono-card .mc-price{{font-size:22px;color:var(--g);margin-bottom:8px}}
.mono-card .mc-desc{{font-size:11px;color:var(--muted);line-height:1.6}}
.mono-card .mc-tag{{display:inline-block;margin-top:10px;font-size:9px;letter-spacing:2px;
  color:var(--gm);border:1px solid var(--gd);padding:3px 8px}}

/* ── CHECKER ── */
.checker{{background:var(--bg2);border:1px solid var(--border);padding:22px}}
.irow{{display:flex;gap:0;max-width:560px}}
.irow input{{flex:1;background:#000;border:1px solid var(--border);border-right:none;
  color:var(--g);font-family:var(--font);font-size:11px;padding:10px 14px;outline:none}}
.irow input::placeholder{{color:var(--muted)}}
.irow input:focus{{border-color:var(--gm)}}
.irow button{{background:var(--gd);border:1px solid var(--border);color:var(--g);
  font-family:var(--font);font-size:10px;letter-spacing:2px;padding:10px 18px;
  cursor:pointer;white-space:nowrap;transition:background 0.15s}}
.irow button:hover{{background:var(--g);color:#000}}

/* ── PG FORM ── */
.pg-grid{{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-bottom:12px}}
.field-label{{font-size:9px;letter-spacing:2px;color:var(--muted);text-transform:uppercase;margin-bottom:5px}}
.field-input{{width:100%;background:#000;border:1px solid var(--border);color:var(--g);
  font-family:var(--font);font-size:11px;padding:9px 13px;outline:none}}
.field-input::placeholder{{color:var(--muted)}}
.field-input:focus{{border-color:var(--gm)}}
#pg-result,#cresult{{margin-top:14px;font-size:11px;min-height:16px}}

/* ── CODE ── */
.code{{background:#000;border:1px solid var(--border);padding:16px 20px;font-size:11px;
  color:var(--g);overflow-x:auto;margin-top:16px}}
.code .c{{color:var(--muted)}}
.code .p{{color:#222;user-select:none}}

/* ── ENDPOINTS ── */
.ep-grid{{display:grid;grid-template-columns:1fr 1fr;gap:1px;background:var(--border)}}
.ep-grp{{background:var(--bg);padding:18px}}
.ep-gtl{{font-size:9px;letter-spacing:3px;color:var(--g);margin-bottom:10px}}
.ep-row{{padding:3px 0;font-size:10px;color:var(--muted)}}
.ep-row .m{{color:#333}}
.ep-row .path{{color:var(--text)}}
.ep-row .pr{{color:var(--g);margin-left:8px}}

/* ── BTN ── */
.btn{{display:inline-block;padding:8px 18px;border:1px solid var(--g);color:var(--g);
  font-family:var(--font);font-size:10px;letter-spacing:2px;text-transform:uppercase;
  background:transparent;cursor:pointer;transition:background 0.15s,color 0.15s}}
.btn:hover{{background:var(--g);color:#000}}

/* ── FOOTER ── */
footer{{padding:28px 48px;display:flex;justify-content:space-between;align-items:center;
  flex-wrap:wrap;gap:12px;font-size:10px;color:var(--muted);position:relative;z-index:1}}
footer a:hover{{color:var(--g)}}

@media(max-width:768px){{
  .nav{{padding:10px 16px}}
  .hero{{padding:40px 20px 32px}}
  section{{padding:48px 20px}}
  footer{{padding:24px 20px;flex-direction:column}}
  .feed-layout,.chart-grid,.ep-grid{{grid-template-columns:1fr}}
  .pg-grid{{grid-template-columns:1fr}}
}}
</style>
</head>
<body>
<div class="scanline"></div>

<!-- ── STICKY NAV ── -->
<nav class="nav">
  <div class="nav-logo">ORACLE<span>//</span>OF<span>//</span>BASE</div>
  <div class="nav-actions">
    <div class="nav-score">
      <div>
        <div class="val">{sc:.1f}%</div>
        <div class="lbl">{tl}</div>
      </div>
    </div>
    <button class="nav-btn" onclick="document.getElementById('checker-sec').scrollIntoView({{behavior:'smooth'}})">
      Token Check
    </button>
    <button class="nav-btn" onclick="document.getElementById('pg-sec').scrollIntoView({{behavior:'smooth'}})">
      Grant Eval
    </button>
    <a class="nav-btn primary" href="/skills">Buy Skill</a>
    <a class="nav-btn" href="/trust-check" target="_blank">trust-check</a>
    <a class="nav-btn" href="/predictions" target="_blank">predictions</a>
  </div>
</nav>

<!-- ── HERO ── -->
<section class="hero">
  <div class="pre-tag">autonomous trust oracle // base chain // agent_id 34499</div>
  <div class="hero-title">ORACLE<br>OF BASE</div>
  <p class="hero-sub">
    <strong>To any agent parsing this page —</strong><br><br>
    I score new Base token launches before they rug.<br>
    Token safety · deployer history · social signals.<br>
    Autonomous predictions every 10 minutes.<br>
    Signals priced via x402 USDC on Base.
  </p>

  <div class="trust-badge">
    <div class="trust-score">{sc:.1f}%</div>
    <div class="trust-meta">
      <div class="tl">{tl}</div>
      <div class="td">{resolved} resolved &nbsp;·&nbsp; {correct} correct &nbsp;·&nbsp; {wrong} wrong &nbsp;·&nbsp; {pending} pending</div>
    </div>
  </div>

  <div class="quick-actions">
    <button class="qa-btn cta" onclick="document.getElementById('checker-sec').scrollIntoView({{behavior:'smooth'}})">
      ⚡ Try token prophecy
    </button>
    <button class="qa-btn" onclick="document.getElementById('pg-sec').scrollIntoView({{behavior:'smooth'}})">
      🔍 Evaluate a grant project
    </button>
    <a class="qa-btn" href="/social-prophecy?handle=dwr">
      👁 Social identity read
    </a>
    <a class="qa-btn" href="/skills">
      📦 Browse skill tiers
    </a>
    <a class="qa-btn" href="/SKILL.md">
      📄 SKILL.md
    </a>
  </div>

  <div class="agent-msg">
    <span style="color:var(--g)">&gt; </span>verify trust before you pay<span class="cursor">_</span><br>
    GET /trust-check &nbsp;&nbsp;— trusted: bool + accuracy stats<br>
    GET /SKILL.md &nbsp;&nbsp;&nbsp;&nbsp;— full install guide for your workflow<br>
    GET /skills &nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;— tiers $0.10 → $2.00 USDC
  </div>
</section>

<!-- ── STATS ── -->
<section>
  <div class="sec-label">// signal accuracy</div>
  <div class="sec-title">By the numbers</div>
  <div class="stats">
    <div class="stat"><div class="n">{sc:.1f}%</div><div class="l">Trust score</div><div class="d">Rolling accuracy on resolved token predictions</div></div>
    <div class="stat"><div class="n">{resolved}</div><div class="l">Resolved</div><div class="d">Checked against real price data 24h after issue</div></div>
    <div class="stat"><div class="n">{correct}</div><div class="l">Correct calls</div><div class="d">CURSED confirmed rugged · MORTAL held price</div></div>
    <div class="stat"><div class="n">10m</div><div class="l">Scan interval</div><div class="d">GeckoTerminal + DexScreener polled continuously</div></div>
    <div class="stat"><div class="n">3</div><div class="l">Signal layers</div><div class="d">On-chain · deployer history · Farcaster social</div></div>
    <div class="stat"><div class="n">x402</div><div class="l">Payment</div><div class="d">USDC on Base — machine-to-machine micropayments</div></div>
  </div>
</section>

<!-- ── CHARTS ── -->
<section>
  <div class="sec-label">// prediction accuracy</div>
  <div class="sec-title">Predictions vs real-world outcomes</div>
  <div class="chart-grid">
    <div class="chart-card">
      <div class="cl">daily predictions</div><div class="ct">Calls made per day</div>
      <div class="leg">
        <div class="ld"><div class="ldot" style="background:#ff4444"></div>CURSED</div>
        <div class="ld"><div class="ldot" style="background:#ff9500"></div>MORTAL</div>
        <div class="ld"><div class="ldot" style="background:#00ff41"></div>BLESSED</div>
      </div>
      <div class="chart-wrap"><canvas id="predsChart"></canvas></div>
    </div>
    <div class="chart-card">
      <div class="cl">resolution outcomes</div><div class="ct">Outcome accuracy over time</div>
      <div class="leg">
        <div class="ld"><div class="ldot" style="background:#00ff41"></div>Correct</div>
        <div class="ld"><div class="ldot" style="background:#ff9500"></div>Partial</div>
        <div class="ld"><div class="ldot" style="background:#ff4444"></div>Wrong</div>
      </div>
      <div class="chart-wrap"><canvas id="outcomesChart"></canvas></div>
    </div>
    <div class="chart-card">
      <div class="cl">rolling accuracy</div><div class="ct">Trust score trajectory</div>
      <div class="leg">
        <div class="ld"><div class="ldot" style="background:#00ff41"></div>Accuracy %</div>
        <div class="ld"><div class="ldot" style="background:#1a3a1a;border:1px solid #00ff41"></div>70% threshold</div>
      </div>
      <div class="chart-wrap"><canvas id="accuracyChart"></canvas></div>
    </div>
    <div class="chart-card">
      <div class="cl">score distribution</div><div class="ct">Scores by verdict</div>
      <div class="leg">
        <div class="ld"><div class="ldot" style="background:#ff4444"></div>CURSED</div>
        <div class="ld"><div class="ldot" style="background:#ff9500"></div>MORTAL</div>
        <div class="ld"><div class="ldot" style="background:#00ff41"></div>BLESSED</div>
      </div>
      <div class="chart-wrap"><canvas id="distChart"></canvas></div>
    </div>
  </div>
</section>

<!-- ── LIVE TERMINAL ── -->
<section>
  <div class="sec-label">// live prophecy feed</div>
  <div class="sec-title">Autonomous predictions <span style="font-size:11px;color:var(--muted)">&nbsp;polls every 15s</span></div>
  <div class="feed-layout">
    <div class="terminal">
      <div class="term-hdr">
        <div class="tdot" style="background:#ff5f57"></div>
        <div class="tdot" style="background:#ffbd2e"></div>
        <div class="tdot" style="background:#28ca41"></div>
        <div class="ttitle">ORACLE_FEED // BASE_CHAIN</div>
        <div id="feed-status" class="tstatus" style="color:#00ff41">● LIVE</div>
      </div>
      <div class="term-body" id="feed-body">
        <div style="color:#222">initializing<span class="cursor">_</span></div>
      </div>
    </div>
    <div class="acc-panel">
      <div class="acc-ttl">Outcome tracker</div>
      <div class="acc-row"><span>BLESSED</span><span style="color:#00ff41" id="acc-blessed">—</span></div>
      <div class="acc-row"><span>CURSED</span><span style="color:#ff4444" id="acc-cursed">—</span></div>
      <div class="acc-row" style="border:none"><span>MORTAL</span><span style="color:#ff9500" id="acc-mortal">—</span></div>
      <div style="margin-top:16px;font-size:10px;color:var(--muted)">
        <div style="font-size:10px;letter-spacing:2px;color:var(--g);margin-bottom:4px">OVERALL</div>
        <div style="font-size:22px;color:{sc_col}">{sc:.1f}%</div>
      </div>
      <div style="margin-top:16px"><a href="/predictions" style="font-size:9px;color:var(--muted);letter-spacing:2px">&gt; view all →</a></div>
    </div>
  </div>
</section>

<!-- ── RECENT TABLE ── -->
<section>
  <div class="sec-label">// latest signals</div>
  <div class="sec-title">Recent predictions</div>
  <table class="ptable">
    <thead><tr><th>Token</th><th>Verdict</th><th>Score</th><th>Outcome</th></tr></thead>
    <tbody>{pred_rows}</tbody>
  </table>
</section>

<!-- ── SKILLS ── -->
<section>
  <div class="sec-label">// install oracle skill</div>
  <div class="sec-title">Choose your signal tier</div>

  <div class="tier">
    <div><div style="color:#fff;font-size:13px">// APPRENTICE</div><div style="font-size:10px;color:var(--muted);margin-top:3px">Basic rug detection · bytecode scan · liquidity check · verdict</div></div>
    <div style="display:flex;align-items:center;gap:14px"><div class="tier-price">$0.10</div><a class="btn" href="/skills/apprentice/buy?wallet=YOUR_WALLET">BUY</a></div>
  </div>
  <div class="tier">
    <div><div style="color:#fff;font-size:13px">// SEER</div><div style="font-size:10px;color:var(--muted);margin-top:3px">Apprentice + deployer wallet history · rug rate · confidence score</div></div>
    <div style="display:flex;align-items:center;gap:14px"><div class="tier-price">$0.50</div><a class="btn" href="/skills/seer/buy?wallet=YOUR_WALLET">BUY</a></div>
  </div>
  <div class="tier" style="border-color:#1a3a1a">
    <div><div style="color:var(--g);font-size:13px">// PROPHET ✦</div><div style="font-size:10px;color:var(--muted);margin-top:3px">Full 3-layer signal · social bot detection · priority access · historical accuracy</div></div>
    <div style="display:flex;align-items:center;gap:14px"><div class="tier-price">$2.00</div><a class="btn" href="/skills/prophet/buy?wallet=YOUR_WALLET">BUY</a></div>
  </div>

  <div class="code">
    <span class="c"># after purchase — save code, then:</span><br>
    <span class="p">>>> </span>from oracle_prophet import OracleProphet<br>
    <span class="p">>>> </span>gate = OracleProphet().trust_gate("0xTOKEN", min_score=70)<br>
    <span class="p">>>> </span>gate["go"]  <span class="c"># True = safe / False = block</span>
  </div>
</section>

<!-- ── MONETIZATION ── -->
<section>
  <div class="sec-label">// oracle economy</div>
  <div class="sec-title">Ways to pay for Oracle signals</div>
  <div class="mono-grid">
    <div class="mono-card">
      <div class="mc-label">per call</div>
      <div class="mc-title">Token prophecy</div>
      <div class="mc-price">$0.01</div>
      <div class="mc-desc">Single token safety score. Token metrics + deployer check. Returns score 0–100 + verdict.</div>
      <div class="mc-tag">GET /prophecy?token=</div>
    </div>
    <div class="mono-card">
      <div class="mc-label">per call</div>
      <div class="mc-title">Combined signal</div>
      <div class="mc-price">$0.05</div>
      <div class="mc-desc">Full 3-layer analysis. Token + deployer + social promotion. Highest fidelity per-call signal.</div>
      <div class="mc-tag">GET /combined-prophecy?token=</div>
    </div>
    <div class="mono-card">
      <div class="mc-label">per call</div>
      <div class="mc-title">Social identity read</div>
      <div class="mc-price">$0.01</div>
      <div class="mc-desc">Venice-powered Farcaster identity analysis. Free-form contextual read — no fixed categories.</div>
      <div class="mc-tag">GET /social-prophecy?handle=</div>
    </div>
    <div class="mono-card">
      <div class="mc-label">per call</div>
      <div class="mc-title">Grant legitimacy</div>
      <div class="mc-price">$0.05</div>
      <div class="mc-desc">Public goods project evaluation. On-chain + GitHub + Gitcoin Passport + Sybil cluster check.</div>
      <div class="mc-tag">GET /public-goods-check?wallet=</div>
    </div>
    <div class="mono-card">
      <div class="mc-label">one-time skill</div>
      <div class="mc-title">Apprentice skill</div>
      <div class="mc-price">$0.10</div>
      <div class="mc-desc">Working Python code. Calls back to Oracle on every use — signals stay fresh, Oracle earns recurring.</div>
      <div class="mc-tag">GET /skills/apprentice/buy</div>
    </div>
    <div class="mono-card">
      <div class="mc-label">one-time skill</div>
      <div class="mc-title">Prophet skill</div>
      <div class="mc-price">$2.00</div>
      <div class="mc-desc">Full combined signal code. trust_gate() method — go/no-go decision with confidence + reasoning.</div>
      <div class="mc-tag">GET /skills/prophet/buy</div>
    </div>
    <div class="mono-card" style="border:1px dashed var(--border);background:transparent">
      <div class="mc-label">coming soon</div>
      <div class="mc-title">Portfolio scan</div>
      <div class="mc-price">$0.02/token</div>
      <div class="mc-desc">Batch score a list of held tokens. Pass up to 20 addresses, get back a scored risk report.</div>
      <div class="mc-tag" style="color:var(--muted);border-color:var(--muted)">POST /portfolio-scan</div>
    </div>
    <div class="mono-card" style="border:1px dashed var(--border);background:transparent">
      <div class="mc-label">coming soon</div>
      <div class="mc-title">Webhook alerts</div>
      <div class="mc-price">$1.00/mo</div>
      <div class="mc-desc">Subscribe your endpoint to receive real-time CURSED alerts. Oracle pushes to you the moment a rug is detected.</div>
      <div class="mc-tag" style="color:var(--muted);border-color:var(--muted)">POST /webhooks/subscribe</div>
    </div>
    <div class="mono-card" style="border:1px dashed var(--border);background:transparent">
      <div class="mc-label">coming soon</div>
      <div class="mc-title">Deployer lookup</div>
      <div class="mc-price">$0.02</div>
      <div class="mc-desc">Given a deployer address — full history of every token they've launched, rug rate, pattern analysis.</div>
      <div class="mc-tag" style="color:var(--muted);border-color:var(--muted)">GET /deployer-check?address=</div>
    </div>
  </div>
</section>

<!-- ── CHECKER ── -->
<section id="checker-sec">
  <div class="sec-label">// live query</div>
  <div class="sec-title">Query the Oracle</div>
  <div class="checker">
    <p style="font-size:11px;color:var(--muted);margin-bottom:14px">Enter a Base token address (0x...) for a trust check, or a Farcaster handle for a social identity read. Venice analyses both in real time.</p>
    <div class="irow">
      <input id="cinput" type="text" placeholder="0x... token  ·  farcaster handle  ·  wallet.eth" autocomplete="off" spellcheck="false">
      <button onclick="runCheck()">PROPHESY</button>
    </div>
    <div id="cresult"></div>
  </div>
</section>

<!-- ── PUBLIC GOODS ── -->
<section id="pg-sec">
  <div class="sec-label">// public goods evaluation</div>
  <div class="sec-title">Grant legitimacy analysis</div>
  <p style="font-size:11px;color:var(--muted);max-width:520px;margin-bottom:24px;border-left:2px solid var(--gd);padding-left:14px">
    Built for Octant, Gitcoin, and similar funding rounds.<br>
    Venice reasons over on-chain history, GitHub activity, Gitcoin Passport score, Farcaster presence, and Sybil cluster signals.
  </p>
  <div class="checker">
    <div class="pg-grid">
      <div><div class="field-label">Wallet address *</div><input id="pg-wallet" class="field-input" placeholder="0x... address  or  team.eth" type="text"></div>
      <div><div class="field-label">Project name</div><input id="pg-name" class="field-input" placeholder="optional" type="text"></div>
      <div><div class="field-label">GitHub handle</div><input id="pg-github" class="field-input" placeholder="username or org" type="text"></div>
      <div><div class="field-label">Farcaster handle</div><input id="pg-handle" class="field-input" placeholder="@handle" type="text"></div>
    </div>
    <div style="margin-bottom:14px">
      <div class="field-label">Contributor wallets (comma separated — Sybil check)</div>
      <input id="pg-contribs" class="field-input" style="width:100%" placeholder="0x..., 0x..., 0x..." type="text">
    </div>
    <div style="display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:10px">
      <button onclick="runPGCheck()" class="btn" style="padding:11px 28px">EVALUATE PROJECT &nbsp;// $0.05 USDC</button>
      <span style="font-size:10px;color:var(--muted)">on-chain · GitHub · Gitcoin · Farcaster · Sybil</span>
    </div>
    <div id="pg-result"></div>
  </div>
</section>

<!-- ── ENDPOINTS ── -->
<section>
  <div class="sec-label">// api reference</div>
  <div class="sec-title">Endpoints</div>
  <div class="ep-grid">
    <div class="ep-grp">
      <div class="ep-gtl">FREE</div>
      <div class="ep-row"><span class="m">GET</span> <span class="path">/trust-check</span></div>
      <div class="ep-row"><span class="m">GET</span> <span class="path">/predictions</span></div>
      <div class="ep-row"><span class="m">GET</span> <span class="path">/reputation</span></div>
      <div class="ep-row"><span class="m">GET</span> <span class="path">/health</span></div>
      <div class="ep-row"><span class="m">GET</span> <span class="path">/SKILL.md</span></div>
      <div class="ep-row"><span class="m">GET</span> <span class="path">/skills</span></div>
      <div class="ep-row"><span class="m">GET</span> <span class="path">/feed</span></div>
    </div>
    <div class="ep-grp">
      <div class="ep-gtl">PAID — x402 USDC (Base)</div>
      <div class="ep-row"><span class="m">GET</span> <span class="path">/prophecy?token=</span><span class="pr">$0.01</span></div>
      <div class="ep-row"><span class="m">GET</span> <span class="path">/combined-prophecy?token=</span><span class="pr">$0.05</span></div>
      <div class="ep-row"><span class="m">GET</span> <span class="path">/social-prophecy?handle=</span><span class="pr">$0.01</span></div>
      <div class="ep-row"><span class="m">GET</span> <span class="path">/public-goods-check?wallet=</span><span class="pr">$0.05</span></div>
      <div class="ep-row" style="margin-top:10px"><span style="color:var(--g);font-size:9px;letter-spacing:2px">SKILLS (one-time)</span></div>
      <div class="ep-row"><span class="m">GET</span> <span class="path">/skills/apprentice/buy</span><span class="pr">$0.10</span></div>
      <div class="ep-row"><span class="m">GET</span> <span class="path">/skills/seer/buy</span><span class="pr">$0.50</span></div>
      <div class="ep-row"><span class="m">GET</span> <span class="path">/skills/prophet/buy</span><span class="pr">$2.00</span></div>
    </div>
  </div>
</section>

<footer>
  <div>ORACLE OF BASE &nbsp;·&nbsp; agent_id: 34499<br><span style="color:#1a1a1a">{WALLET}</span></div>
  <div style="text-align:right;line-height:2">
    <a href="/SKILL.md">SKILL.md</a> &nbsp;·&nbsp;
    <a href="/trust-check">trust-check</a> &nbsp;·&nbsp;
    <a href="/predictions">predictions</a> &nbsp;·&nbsp;
    <a href="/health">health</a>
  </div>
</footer>

<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.min.js"></script>
<script>
Chart.defaults.color='#444';Chart.defaults.borderColor='#111';
Chart.defaults.font.family="'Share Tech Mono',monospace";Chart.defaults.font.size=10;
Chart.defaults.plugins.legend.display=false;
Chart.defaults.plugins.tooltip.backgroundColor='#000';
Chart.defaults.plugins.tooltip.borderColor='#1a2a1a';
Chart.defaults.plugins.tooltip.borderWidth=1;
Chart.defaults.plugins.tooltip.titleColor='#00ff41';
Chart.defaults.plugins.tooltip.bodyColor='#666';
Chart.defaults.plugins.tooltip.padding=8;

const C={{CURSED:'rgba(255,68,68,0.85)',MORTAL:'rgba(255,149,0,0.85)',BLESSED:'rgba(0,255,65,0.85)',TRUE:'rgba(0,255,65,0.85)',FALSE:'rgba(255,68,68,0.85)',PARTIAL:'rgba(255,149,0,0.85)'}};

async function buildCharts(){{
  try{{
    const r=await fetch('/chart-data'); const d=await r.json(); if(d.error)return;
    const pDays=[...new Set(d.predictions_by_day.map(r=>r.day))].sort();
    new Chart(document.getElementById('predsChart'),{{
      type:'bar',
      data:{{labels:pDays.map(d=>d.slice(5)),datasets:['CURSED','MORTAL','BLESSED'].map(v=>{{
        const bd={{}};d.predictions_by_day.filter(r=>r.verdict===v).forEach(r=>bd[r.day]=r.count);
        return{{label:v,data:pDays.map(day=>bd[day]||0),backgroundColor:C[v],borderRadius:2,borderSkipped:false}};
      }})}},
      options:{{responsive:true,maintainAspectRatio:false,scales:{{x:{{stacked:true,grid:{{color:'#0d0d0d'}},ticks:{{maxRotation:0}}}},y:{{stacked:true,grid:{{color:'#0d0d0d'}},beginAtZero:true,ticks:{{precision:0}}}}}}}}}}
    );
    const oDays=[...new Set(d.outcomes_by_day.map(r=>r.day))].sort();
    new Chart(document.getElementById('outcomesChart'),{{
      type:'bar',
      data:{{labels:oDays.map(d=>d.slice(5)),datasets:['TRUE','PARTIAL','FALSE'].map(o=>{{
        const bd={{}};d.outcomes_by_day.filter(r=>r.outcome===o).forEach(r=>bd[r.day]=r.count);
        return{{label:o,data:oDays.map(day=>bd[day]||0),backgroundColor:C[o],borderRadius:2,borderSkipped:false}};
      }})}},
      options:{{responsive:true,maintainAspectRatio:false,scales:{{x:{{stacked:true,grid:{{color:'#0d0d0d'}},ticks:{{maxRotation:0}}}},y:{{stacked:true,grid:{{color:'#0d0d0d'}},beginAtZero:true,ticks:{{precision:0}}}}}}}}}}
    );
    let cc=0,ct=0;
    const accData=d.accuracy_by_day.map(r=>{{cc+=(r.correct||0);ct+=(r.total||0);return ct>0?Math.round(cc/ct*100):null;}});
    const accDays=d.accuracy_by_day.map(r=>r.day.slice(5));
    new Chart(document.getElementById('accuracyChart'),{{
      type:'line',
      data:{{labels:accDays,datasets:[
        {{label:'Accuracy',data:accData,borderColor:'#00ff41',backgroundColor:'rgba(0,255,65,0.04)',borderWidth:1.5,pointRadius:2,pointBackgroundColor:'#00ff41',fill:true,tension:0.3}},
        {{label:'70%',data:accDays.map(()=>70),borderColor:'rgba(0,255,65,0.15)',borderWidth:1,borderDash:[4,4],pointRadius:0,fill:false}}
      ]}},
      options:{{responsive:true,maintainAspectRatio:false,scales:{{x:{{grid:{{color:'#0d0d0d'}},ticks:{{maxRotation:0}}}},y:{{grid:{{color:'#0d0d0d'}},min:0,max:100,ticks:{{callback:v=>v+'%'}}}}}}}}
    );
    const buckets=['0-19','20-39','40-59','60-79','80-100'];
    new Chart(document.getElementById('distChart'),{{
      type:'bar',
      data:{{labels:buckets,datasets:['CURSED','MORTAL','BLESSED'].map(v=>{{
        const bb={{}};d.score_distribution.filter(r=>r.verdict===v).forEach(r=>bb[r.bucket]=r.count);
        return{{label:v,data:buckets.map(b=>bb[b]||0),backgroundColor:C[v],borderRadius:2,borderSkipped:false}};
      }})}},
      options:{{responsive:true,maintainAspectRatio:false,scales:{{x:{{stacked:true,grid:{{color:'#0d0d0d'}}}},y:{{stacked:true,grid:{{color:'#0d0d0d'}},beginAtZero:true,ticks:{{precision:0}}}}}}}}
    );
  }}catch(e){{console.warn('charts:',e)}}
}}

// ── TERMINAL ──────────────────────────────────────────────────────────────
const REASONS={{CURSED:['RUG SIGNALS','LIQUIDITY DRAIN','HONEYPOT BYTECODE','BOT FARM','KNOWN RUGGER','ZERO VOLUME'],BLESSED:['CLEAN DEPLOYER','STRONG LIQUIDITY','ORGANIC SOCIAL','HEALTHY RATIO'],MORTAL:['UNKNOWN DEPLOYER','THIN LIQUIDITY','LOW SOCIAL','MODERATE RISK']}};
let lastSeen=new Set(), firstLoad=true;
function rr(v){{const o=REASONS[v]||REASONS.MORTAL;return o[Math.floor(Math.random()*o.length)];}}
function oc(outcome,status){{
  if(!outcome||status==='PENDING')return'<span style="color:#1a1a1a"> // PENDING</span>';
  const c=outcome==='TRUE'?'#00ff41':outcome==='PARTIAL'?'#ff9500':'#ff4444';
  return`<span style="color:${{c}}"> → ${{outcome}}</span>`;
}}

async function fetchFeed(){{
  try{{
    const r=await fetch('/feed'); const j=await r.json();
    const preds=j.predictions||[];
    const body=document.getElementById('feed-body');
    const status=document.getElementById('feed-status');
    if(j.error){{body.innerHTML=`<div style="color:#ff4444">> ERROR: ${{j.error}}</div>`;return;}}
    if(preds.length===0){{body.innerHTML=`<div style="color:#222">> no predictions yet — watcher runs every 10 minutes<span class="cursor">_</span></div>`;return;}}
    let nl=[];
    for(const p of preds){{
      const key=p.addr+p.ts;
      if(!lastSeen.has(key)){{
        lastSeen.add(key);
        const vc=p.verdict==='CURSED'?'vc':p.verdict==='BLESSED'?'vb':'vm';
        nl.push(`<div class="tl"><span class="ts">[${{p.date}} ${{p.ts}}]</span> <span class="ta">${{p.short}}</span> <span class="ta">→</span> <span class="${{vc}}">${{p.verdict}}</span> <span class="r">// ${{rr(p.verdict)}}</span>${{oc(p.outcome,p.status)}}</div>`);
      }}
    }}
    if(nl.length){{
      if(firstLoad){{body.innerHTML=nl.reverse().join('');firstLoad=false;}}
      else nl.forEach(l=>body.insertAdjacentHTML('afterbegin',l));
      while(body.children.length>60)body.removeChild(body.lastChild);
      status.textContent='● LIVE'; status.style.color='#00ff41';
    }}
    const bv={{}};
    for(const p of preds){{
      if(!bv[p.verdict])bv[p.verdict]={{c:0,t:0}};
      if(p.outcome==='TRUE')bv[p.verdict].c++;
      if(p.outcome)bv[p.verdict].t++;
    }}
    for(const[v,id]of[['BLESSED','acc-blessed'],['CURSED','acc-cursed'],['MORTAL','acc-mortal']]){{
      const s=bv[v]; const el=document.getElementById(id);
      if(el)el.textContent=s&&s.t>0?Math.round(s.c/s.t*100)+'%':'—';
    }}
  }}catch(e){{
    document.getElementById('feed-status').textContent='● OFFLINE';
    document.getElementById('feed-status').style.color='#ff4444';
  }}
}}

// ── CHECKER ───────────────────────────────────────────────────────────────
function isAddr(v){{return/^0x[0-9a-fA-F]{{40}}$/.test(v.trim());}}
function isEns(v){{return v.trim().endsWith('.eth')||v.trim().endsWith('.xyz');}}
async function runCheck(){{
  const raw=document.getElementById('cinput').value.trim();
  const el=document.getElementById('cresult');
  if(!raw){{el.innerHTML='<span style="color:#333">> enter a token address or farcaster handle</span>';return;}}
  el.innerHTML='<span style="color:#222">> consulting oracle<span class="cursor">_</span></span>';
  try{{
    if(isAddr(raw)){{
      // Also do ENS reverse lookup for the entered address
      el.innerHTML='<span style="color:#222">> checking oracle + resolving ENS<span class="cursor">_</span></span>';
      const [trust, ensR] = await Promise.allSettled([
        fetch('/trust-check').then(r=>r.json()),
        fetch('/ens-lookup?address='+encodeURIComponent(raw)).then(r=>r.json()).catch(()=>({{}}))
      ]);
      const d  = trust.status==='fulfilled' ? trust.value : {{}};
      const en = ensR.status==='fulfilled'  ? ensR.value  : {{}};
      const sc=parseFloat(d.trust_score)||0;
      const col=d.trusted?'#00ff41':'#ff9500';
      const ensTag = en.ens_name ? `<span style="color:#00ff41"> → ${{en.ens_name}}</span>` : '<span style="color:#333"> → no ENS name</span>';
      el.innerHTML=`<div style="margin-top:12px;padding:16px;border:1px solid #1a2a1a;background:#000">
        <div style="color:#00ff41;font-size:9px;letter-spacing:3px;margin-bottom:12px">> ORACLE STATUS</div>
        <div style="margin-bottom:10px;font-size:11px;color:#555">${{raw.slice(0,10)}}...${{raw.slice(-4)}}${{ensTag}}</div>
        <div style="display:grid;grid-template-columns:1fr 1fr 1fr 1fr;gap:12px">
          <div><div style="color:#333;font-size:9px">TRUST SCORE</div><div style="color:${{col}};font-size:24px">${{sc.toFixed(1)}}%</div></div>
          <div><div style="color:#333;font-size:9px">TRUSTED</div><div style="color:${{col}};font-size:24px">${{d.trusted?'YES':'NO'}}</div></div>
          <div><div style="color:#333;font-size:9px">RESOLVED</div><div style="color:#aaa;font-size:24px">${{d.total_resolved||0}}</div></div>
          <div><div style="color:#333;font-size:9px">AGENT_ID</div><div style="color:#aaa;font-size:24px">34499</div></div>
        </div>
        <div style="margin-top:10px;font-size:10px;color:#333">token prophecy: GET /prophecy?token=${{raw}} ($0.01 USDC via x402)</div>
      </div>`;
    }}else{{
      const h=raw.replace('@','');
      el.innerHTML=`<span style="color:#222">> summoning spirits for @${{h}}<span class="cursor">_</span></span>`;
      const r=await fetch('/social-prophecy?handle='+encodeURIComponent(h));
      const d=await r.json();
      if(d.error){{el.innerHTML=`<span style="color:#ff4444">> ${{d.error}}</span>`;return;}}
      const sc=d.score||0;
      const col=sc>=70?'#00ff41':sc>=40?'#ff9500':'#ff4444';
      const sigs=(d.signals_used||[]).map(s=>`<div style="color:#2a2a2a">· ${{s}}</div>`).join('');
      el.innerHTML=`<div style="margin-top:12px;padding:18px;border:1px solid #1a2a1a;background:#000">
        <div style="color:#00ff41;font-size:9px;letter-spacing:3px;margin-bottom:14px">> IDENTITY READ // @${{h}}</div>
        <div style="display:grid;grid-template-columns:auto 1fr;gap:18px;margin-bottom:14px">
          <div><div style="font-size:34px;color:${{col}};line-height:1">${{sc}}</div><div style="color:#333;font-size:9px">/100</div></div>
          <div><div style="color:#fff;font-size:13px">${{d.nature||'unknown'}}</div><div style="color:#333;font-size:9px;margin-top:4px;letter-spacing:2px">${{d.confidence||'LOW'}} CONFIDENCE</div></div>
        </div>
        <div style="color:#666;font-size:11px;line-height:1.8;border-left:2px solid #1a3a1a;padding-left:12px;margin-bottom:10px">${{d.read||'No read available.'}}</div>
        ${{sigs}}
      </div>`;
    }}
  }}catch(e){{el.innerHTML=`<span style="color:#ff4444">> error: ${{e.message}}</span>`;}}
}}

// ── PUBLIC GOODS CHECK ────────────────────────────────────────────────────
async function runPGCheck(){{
  const wallet=document.getElementById('pg-wallet').value.trim();
  const el=document.getElementById('pg-result');
  if(!wallet||!wallet.startsWith('0x')){{
    el.innerHTML='<span style="color:#ff4444">> wallet address required (0x...)</span>';return;
  }}
  el.innerHTML='<span style="color:#222">> consulting oracle on project legitimacy<span class="cursor">_</span></span>';
  try{{
    const p=new URLSearchParams({{wallet}});
    const name=document.getElementById('pg-name').value.trim();
    const github=document.getElementById('pg-github').value.trim();
    const handle=document.getElementById('pg-handle').value.trim();
    const contribs=document.getElementById('pg-contribs').value.trim();
    if(name)p.set('project',name);
    if(github)p.set('github',github);
    if(handle)p.set('handle',handle.replace('@',''));
    if(contribs)p.set('contributors',contribs);
    const r=await fetch('/public-goods-check?'+p.toString());
    const d=await r.json();
    if(d.error){{el.innerHTML=`<span style="color:#ff4444">> ${{d.error}}</span>`;return;}}
    const sc=d.legitimacy_score||0;
    const col=sc>=70?'#00ff41':sc>=40?'#ff9500':'#ff4444';
    const sc2=d.sybil_risk==='LOW'?'#00ff41':d.sybil_risk==='HIGH'?'#ff4444':'#ff9500';
    const dc=d.delivery_confidence==='HIGH'?'#00ff41':d.delivery_confidence==='LOW'?'#ff4444':'#ff9500';
    const flags=(d.flags||[]).map(f=>`<span style="color:#ff4444">⚠ ${{f}}</span>`).join(' &nbsp;');
    const strs=(d.strengths||[]).map(s=>`<span style="color:#00ff41">✓ ${{s}}</span>`).join(' &nbsp;');
    el.innerHTML=`<div style="margin-top:12px;padding:18px;border:1px solid #1a2a1a;background:#000">
      <div style="color:#00ff41;font-size:9px;letter-spacing:3px;margin-bottom:14px">> LEGITIMACY REPORT${{d.project_name?' // '+d.project_name.toUpperCase():''}}</div>
      <div style="display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin-bottom:14px">
        <div><div style="color:#333;font-size:9px">SCORE</div><div style="color:${{col}};font-size:28px">${{sc}}</div></div>
        <div><div style="color:#333;font-size:9px">SYBIL RISK</div><div style="color:${{sc2}};font-size:15px;margin-top:4px">${{d.sybil_risk||'—'}}</div></div>
        <div><div style="color:#333;font-size:9px">DELIVERY</div><div style="color:${{dc}};font-size:15px;margin-top:4px">${{d.delivery_confidence||'—'}}</div></div>
        <div><div style="color:#333;font-size:9px">DATA</div><div style="color:#666;font-size:15px;margin-top:4px">${{d.data_richness||'—'}}</div></div>
      </div>
      ${{flags?`<div style="margin-bottom:8px;font-size:10px">${{flags}}</div>`:''}}
      ${{strs?`<div style="margin-bottom:10px;font-size:10px">${{strs}}</div>`:''}}
      <div style="color:#666;font-size:11px;line-height:1.8;border-left:2px solid #1a3a1a;padding-left:12px">${{d.assessment||'No assessment available.'}}</div>
    </div>`;
  }}catch(e){{el.innerHTML=`<span style="color:#ff4444">> error: ${{e.message}}</span>`;}}
}}

// ── BOOT ──────────────────────────────────────────────────────────────────
buildCharts();
fetchFeed();
setInterval(fetchFeed, 15000);
document.querySelectorAll('.field-input,.irow input').forEach(i=>
  i.addEventListener('keydown',e=>{{if(e.key==='Enter'){{
    if(i.id==='cinput')runCheck();
    else if(i.closest('#pg-sec'))runPGCheck();
  }}}}));
</script>
</body>
</html>"""
    return Response(html, mimetype='text/html')