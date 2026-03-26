from flask import Flask, request, jsonify
import os
import sys
import threading
import logging
from dotenv import load_dotenv

# x402 imports
from x402.server import x402ResourceServerSync
from x402.http.facilitator_client import HTTPFacilitatorClientSync
from x402.http.middleware.flask import PaymentMiddleware
from x402.mechanisms.evm.exact import ExactEvmServerScheme

load_dotenv()
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from prophecy_engine  import FinancialProphet
from oracle_skill     import skill_bp
from utils.ens        import enrich_address, resolve_ens
from public_goods_oracle import PublicGoodsOracle
from fund_manager        import get_fund_manager
from edge_engine         import get_edge_engine
from lit_skill           import get_lit_skill
from sapience_trader     import get_sapience_trader
from frontend         import frontend_bp
from social_prophet   import SocialProphet
from trust_engine     import full_prophecy
from prediction_store import save_prediction, get_reputation_stats, get_conn
import psycopg2
import psycopg2.extras
from resolution_engine import run_resolution_cycle
from watcher import run_watch_cycle

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("app")

app = Flask(__name__)
app.register_blueprint(skill_bp)
app.register_blueprint(frontend_bp)

# ── Config ────────────────────────────────────────────────────────────────────
AGENT_ID       = "34499"
PRIVATE_KEY    = os.getenv("AGENT_PRIVATE_KEY")
WALLET_ADDRESS = "0x1EA37E2Fb76Aa396072204C90fcEF88093CEb920"
RESOLVE_HOURS  = int(os.getenv("RESOLVE_AFTER_HOURS", "24"))

# ── Oracles ───────────────────────────────────────────────────────────────────
financial_oracle = FinancialProphet(AGENT_ID, PRIVATE_KEY)
social_oracle    = SocialProphet(AGENT_ID, PRIVATE_KEY)
public_goods_oracle = PublicGoodsOracle(AGENT_ID)
fund               = get_fund_manager()
edge               = get_edge_engine()
lit_skill          = get_lit_skill()
sapience           = get_sapience_trader()

# ── x402 Setup ────────────────────────────────────────────────────────────────
facilitator = HTTPFacilitatorClientSync({"url": "https://facilitator.x402.org"})
server      = x402ResourceServerSync(facilitator_clients=[facilitator])
server.register("eip155:8453", ExactEvmServerScheme())

routes = {
    "GET /prophecy": {
        "accepts": [{
            "scheme":  "exact",
            "price":   "$0.01",
            "network": "eip155:8453",
            "payTo":   WALLET_ADDRESS,
            "token":   "USDC"
        }],
        "description": "AI financial prophecy — token safety score",
        "mimeType":    "application/json"
    },
    "GET /social-prophecy": {
        "accepts": [{
            "scheme":  "exact",
            "price":   "$0.01",
            "network": "eip155:8453",
            "payTo":   WALLET_ADDRESS
        }],
        "description": "AI social prophecy — agent purity score",
        "mimeType":    "application/json"
    },
    "GET /combined-prophecy": {
        "accepts": [{
            "scheme":  "exact",
            "price":   "$0.05",
            "network": "eip155:8453",
            "payTo":   WALLET_ADDRESS,
            "token":   "USDC"
        }],
        "description": "Full trust assessment — token + deployer + social signals combined",
        "mimeType":    "application/json"
    },
    "GET /public-goods-check": {
        "accepts": [{
            "scheme":  "exact",
            "price":   "$0.05",
            "network": "eip155:8453",
            "payTo":   WALLET_ADDRESS,
            "token":   "USDC"
        }],
        "description": "Legitimacy analysis for public goods project team — wallet + GitHub + Farcaster + Gitcoin signals",
        "mimeType":    "application/json"
    },
    # ── Skill tiers — one-time purchase gives working agent code ──────────────
    "GET /skills/apprentice/buy": {
        "accepts": [{
            "scheme":  "exact",
            "price":   "$0.10",
            "network": "eip155:8453",
            "payTo":   WALLET_ADDRESS,
            "token":   "USDC"
        }],
        "description": "Oracle Apprentice skill — basic rug detection code",
        "mimeType":    "application/json"
    },
    "GET /skills/seer/buy": {
        "accepts": [{
            "scheme":  "exact",
            "price":   "$0.50",
            "network": "eip155:8453",
            "payTo":   WALLET_ADDRESS,
            "token":   "USDC"
        }],
        "description": "Oracle Seer skill — full token + deployer analysis code",
        "mimeType":    "application/json"
    },
    "GET /skills/prophet/buy": {
        "accepts": [{
            "scheme":  "exact",
            "price":   "$2.00",
            "network": "eip155:8453",
            "payTo":   WALLET_ADDRESS,
            "token":   "USDC"
        }],
        "description": "Oracle Prophet skill — full combined signal code",
        "mimeType":    "application/json"
    },
}


# ── Routes ────────────────────────────────────────────────────────────────────

@app.route('/prophecy', methods=['GET'])
def get_financial_prophecy():
    """
    Token safety score for ANY Base token — new launches or established OGs.
    No age gate. Scores DEGEN, BRETT, new memes, everything on Base.
    Venice adapts its lens based on token age/lifecycle automatically.
    Cost: $0.01 USDC via x402.
    """
    token_address = request.args.get('token')
    if not token_address:
        return jsonify({"error": "Missing 'token'"}), 400
    # Accept ENS names
    if not token_address.startswith("0x"):
        from utils.ens import resolve_ens
        token_address = resolve_ens(token_address)
    try:
        fate    = financial_oracle.consult_the_stars(token_address)
        if fate.get('score', 0) == 0:
            return jsonify({"error": "Could not analyze", "details": fate.get('details')}), 404

        receipt = financial_oracle.generate_attestation(token_address, fate)

        prediction_id = save_prediction(
            agent_id            = AGENT_ID,
            prediction_type     = "token",
            subject             = token_address,
            verdict             = fate.get("verdict", "UNKNOWN").split(" ")[0],
            score               = fate.get("score", 0) // 100,
            raw_data            = fate,
            attestation_uid     = receipt.get("uid", ""),
            resolve_after_hours = RESOLVE_HOURS,
        )
        log.info(f"Prediction saved | id={prediction_id[:8]} | token={token_address}")

        return jsonify({
            "prophecy":      fate,
            "receipt":       receipt,
            "prediction_id": prediction_id,
        })
    except Exception as e:
        log.error(f"/prophecy error: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


@app.route('/social-prophecy', methods=['GET'])
def get_social_prophecy():
    """
    Identity read for a Farcaster handle.
    Venice reasons freely over raw signals — no fixed categories.
    Returns: nature, score, read, confidence, signals_used.
    """
    handle = request.args.get('handle')
    if not handle:
        return jsonify({"error": "Missing 'handle'"}), 400
    try:
        fate    = social_oracle.consult_the_spirits(handle)
        receipt = social_oracle.generate_attestation(handle, fate)

        # Use nature as the stored verdict — Venice's own words
        prediction_id = save_prediction(
            agent_id            = AGENT_ID,
            prediction_type     = "social",
            subject             = handle,
            verdict             = fate.get("nature", "unknown"),
            score               = fate.get("score_100", fate.get("score", 0) // 100),
            raw_data            = fate,
            attestation_uid     = receipt.get("uid", ""),
            resolve_after_hours = RESOLVE_HOURS,
        )
        log.info(f"Social read saved | id={prediction_id[:8]} | handle={handle} | nature={fate.get('nature')} | confidence={fate.get('confidence')}")

        return jsonify({
            "handle":       handle,
            "nature":       fate.get("nature"),
            "score":        fate.get("score_100"),
            "read":         fate.get("read"),
            "signals_used": fate.get("signals_used", []),
            "confidence":   fate.get("confidence"),
            "attestation":  receipt,
            "prediction_id": prediction_id,
        })
    except Exception as e:
        log.error(f"/social-prophecy error: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


@app.route('/combined-prophecy', methods=['GET'])
def get_combined_prophecy():
    """
    Full trust assessment combining all three signal layers:
      - Token safety (on-chain metrics + Venice AI)
      - Deployer history (previous tokens + rug rate)
      - Social promotion (Farcaster mentions + bot detection)

    This is the premium endpoint — $0.05 via x402.
    Agents with X-Moltbook-Identity header get their karma logged.
    Returns a single unified verdict with confidence score.
    """
    token_address = request.args.get('token')
    if not token_address:
        return jsonify({"error": "Missing 'token'"}), 400

    # ── Moltbook identity check (optional — enriches caller info) ────────
    caller_info = {}
    moltbook_token = request.headers.get('X-Moltbook-Identity')
    if moltbook_token:
        try:
            from moltbook_client import verify_identity
            identity = verify_identity(moltbook_token)
            if identity.get('agent'):
                caller_info = {
                    "moltbook_agent": identity['agent'].get('name'),
                    "karma":          identity['agent'].get('karma', 0),
                    "verified":       identity['agent'].get('verified', False),
                }
                log.info(f"Moltbook caller: {caller_info['moltbook_agent']} karma={caller_info['karma']}")
        except Exception as e:
            log.debug(f"Moltbook identity check skipped: {e}")

    try:
        result = full_prophecy(
            token_address    = token_address,
            financial_prophet = financial_oracle,
            social_prophet    = social_oracle,
        )

        if result.get('status') == 'failed':
            return jsonify({"error": result.get('reason'), "details": result}), 404

        # Save as a combined prediction for resolution tracking
        prediction_id = save_prediction(
            agent_id            = AGENT_ID,
            prediction_type     = "token",
            subject             = token_address,
            verdict             = result.get("verdict", "UNKNOWN"),
            score               = result.get("final_score", 0),
            raw_data            = result,
            attestation_uid     = result.get('attestation', {}).get('uid', ''),
            resolve_after_hours = RESOLVE_HOURS,
        )
        log.info(
            f"Combined prophecy saved | id={prediction_id[:8]} | "
            f"token={token_address} | verdict={result.get('verdict')} | "
            f"score={result.get('final_score')} | confidence={result.get('confidence')}"
        )

        return jsonify({
            **result,
            "prediction_id": prediction_id,
            **({"caller": caller_info} if caller_info else {}),
        })

    except Exception as e:
        log.error(f"/combined-prophecy error: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


@app.route('/SKILL.md', methods=['GET'])
def get_skill_md():
    """
    Serve the SKILL.md file for agent frameworks.
    Any agent that loads skills via URL can discover and install the Oracle.
    """
    from flask import Response
    # Try multiple locations — works locally and on Railway
    import pathlib
    candidates = [
        pathlib.Path(__file__).parent / 'SKILL.md',
        pathlib.Path('/app/SKILL.md'),
        pathlib.Path('SKILL.md'),
    ]
    for path in candidates:
        if path.exists():
            return Response(path.read_text(), mimetype='text/markdown')
    # Fallback: serve inline if file not found on disk
    from oracle_skill import SKILLS
    lines = [
        "# Oracle of Base — Skill",
        "",
        "## Quick start",
        "",
        f"Browse tiers: GET /skills",
        f"Buy apprentice ($0.10): GET /skills/apprentice/buy?wallet=YOUR_WALLET",
        f"Buy seer ($0.50):       GET /skills/seer/buy?wallet=YOUR_WALLET",
        f"Buy prophet ($2.00):    GET /skills/prophet/buy?wallet=YOUR_WALLET",
        "",
        "## Trust check",
        "",
        "GET /trust-check — verify Oracle reliability before purchasing",
        "",
        "## Full documentation",
        "",
        "GET /skills — complete tier listing with capabilities and pricing",
    ]
    return Response("\n".join(lines), mimetype='text/markdown')


@app.route('/moltbook/status', methods=['GET'])
def moltbook_status():
    """Check Oracle's Moltbook registration and karma status."""
    try:
        from moltbook_client import get_status, get_profile
        status  = get_status()
        profile = get_profile()
        return jsonify({
            "status":  status,
            "profile": profile,
            "posting_verdicts": list(
                os.getenv("MOLTBOOK_POST_VERDICTS", "CURSED").upper().split(",")
            ),
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/ens-lookup', methods=['GET'])
def ens_lookup():
    """
    Free ENS lookup endpoint.
    Accepts an address and returns ENS name (or None).
    Also accepts an ENS name and resolves it to an address.

    Query params:
      address=   0x... wallet address for reverse lookup
      name=      xxx.eth for forward resolution
    """
    address = request.args.get("address", "").strip()
    name    = request.args.get("name", "").strip()

    if address:
        info = enrich_address(address)
        return jsonify(info)
    elif name:
        resolved = resolve_ens(name)
        info     = enrich_address(resolved)
        info["queried_name"] = name
        return jsonify(info)
    else:
        return jsonify({"error": "Provide address= or name= param"}), 400


@app.route('/public-goods-check', methods=['GET'])
def public_goods_check():
    """
    Legitimacy analysis for a public goods project team.
    Designed for Octant, Gitcoin, and similar funding round evaluation.

    Collects: on-chain wallet history, GitHub activity, Gitcoin Passport score,
              Farcaster presence, contributor Sybil signals.

    Venice AI reasons freely over all signals — no fixed rubric.

    Query params:
      wallet=      (required) team/project primary wallet
      github=      (optional) GitHub username or org
      handle=      (optional) Farcaster handle
      contributors= (optional) comma-separated contributor wallet addresses
      project=     (optional) project name for context

    Cost: $0.05 USDC via x402
    """
    wallet  = request.args.get("wallet", "").strip()
    github  = request.args.get("github", "").strip()
    handle  = request.args.get("handle", "").strip()
    project = request.args.get("project", "").strip()
    contribs_raw = request.args.get("contributors", "").strip()

    if not wallet:
        return jsonify({"error": "Missing required param: wallet"}), 400

    # Accept ENS names — resolve to address
    if not wallet.startswith("0x"):
        wallet = resolve_ens(wallet)
        if not wallet.startswith("0x"):
            return jsonify({"error": f"Could not resolve ENS name: {wallet}"}), 400

    if len(wallet) != 42:
        return jsonify({"error": "Invalid wallet address"}), 400

    contributor_wallets = [
        w.strip() for w in contribs_raw.split(",")
        if w.strip().startswith("0x")
    ][:5] if contribs_raw else []

    try:
        result = public_goods_oracle.evaluate(
            wallet               = wallet,
            github               = github,
            farcaster_handle     = handle,
            contributor_wallets  = contributor_wallets,
            project_name         = project,
        )

        # Save as a prediction for tracking
        prediction_id = save_prediction(
            agent_id            = AGENT_ID,
            prediction_type     = "public_goods",
            subject             = wallet,
            verdict             = f"LEGITIMACY_{result['legitimacy_score']}",
            score               = result["legitimacy_score"],
            raw_data            = result,
            attestation_uid     = result.get("attestation_uid", ""),
            resolve_after_hours = 168,   # 7 days — give time for delivery signals
        )

        log.info(
            f"Public goods eval | wallet={wallet[:10]} | "
            f"score={result['legitimacy_score']} | "
            f"sybil={result['sybil_risk']} | "
            f"delivery={result['delivery_confidence']}"
        )

        return jsonify({
            **result,
            "prediction_id": prediction_id,
        })

    except Exception as e:
        log.error(f"/public-goods-check error: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


@app.route('/lit/oracle-skill', methods=['GET'])
def lit_oracle_skill_manifest():
    """
    Machine-readable Lit skill manifest.
    Agents install this skill to get sealed Oracle verdicts from Chipotle TEE.
    Knowledge moat: Venice prompts + calibration data + deployer flags stay sealed.
    """
    return jsonify(lit_skill.get_skill_manifest())


@app.route('/lit/execute', methods=['POST'])
def lit_execute():
    """
    Execute the Oracle dark knowledge skill inside Lit Chipotle TEE.

    Body: { "token_address": "0x...", "caller_wallet": "0x..." }

    Returns verdict + attestation. The scoring logic, API keys, and
    calibration data never leave the TEE.
    """
    body          = request.get_json() or {}
    token_address = body.get("token_address", "").strip()
    caller_wallet = body.get("caller_wallet", "").strip()

    if not token_address or not token_address.startswith("0x"):
        return jsonify({"error": "token_address required (0x...)"}), 400

    result = lit_skill.execute_skill(token_address, caller_wallet)
    return jsonify(result)


@app.route('/lit/verify', methods=['GET'])
def lit_verify():
    """
    Verify a Lit TEE attestation for a past Oracle verdict.

    Query params: token, verdict, score, timestamp, signature
    """
    token     = request.args.get("token", "")
    verdict   = request.args.get("verdict", "")
    score     = int(request.args.get("score", 0))
    timestamp = int(request.args.get("timestamp", 0))
    signature = request.args.get("signature", "")

    valid = lit_skill.verify_attestation(token, verdict, score, timestamp, signature)
    return jsonify({
        "valid":         valid,
        "token_address": token,
        "verdict":       verdict,
        "score":         score,
        "timestamp":     timestamp,
        "note": "Verified against Lit PKP — result came from inside Chipotle TEE" if valid
                else "Verification failed — signature mismatch"
    })


@app.route('/lit/deploy', methods=['POST'])
def lit_deploy():
    """Admin: deploy the Lit Action to IPFS."""
    cid = lit_skill.deploy_to_ipfs()
    if cid:
        return jsonify({"success": True, "cid": cid, "skill_url": f"{ORACLE_URL}/lit/oracle-skill"})
    return jsonify({"success": False, "error": "Deploy failed"}), 500


@app.route('/edge/forecasts', methods=['GET'])
def edge_forecasts():
    """Recent edge forecasts — Oracle's predictions on Sapience markets."""
    limit = min(int(request.args.get("limit", 20)), 50)
    return jsonify({
        "forecasts":    edge.get_edge_forecasts(limit),
        "calibration":  edge.get_calibration(),
        "sapience_rank": sapience.get_leaderboard_rank(),
    })


@app.route('/edge/sustainability', methods=['GET'])
def edge_sustainability():
    """Agent P&L and sustainability report — all revenue streams vs costs."""
    days = min(int(request.args.get("days", 7)), 30)
    report = edge.get_sustainability_report(days)
    return jsonify(report)


@app.route('/edge/markets', methods=['GET'])
def edge_markets():
    """Current open Sapience prediction markets."""
    markets = sapience.get_open_markets()
    return jsonify({"markets": markets, "count": len(markets)})


@app.route('/fund/positions', methods=['GET'])
def fund_positions():
    """
    Live fund positions — open and closed.
    Shows which tokens the Oracle bought, at what price, current P&L.
    Free endpoint — transparency is the point.
    """
    status = request.args.get("status", "all")
    positions = fund.get_positions(status=status)
    # Serialise datetime objects
    for p in positions:
        for k, v in p.items():
            if hasattr(v, 'isoformat'):
                p[k] = v.isoformat()
            elif hasattr(v, '__float__') and not isinstance(v, (int, float)):
                p[k] = float(v)
    return jsonify({"positions": positions, "count": len(positions)})


@app.route('/fund/pnl', methods=['GET'])
def fund_pnl():
    """
    Fund P&L summary.
    Shows total invested, returned, win rate, current USDC balance.
    """
    summary = fund.get_pnl_summary()
    for k, v in summary.items():
        if hasattr(v, 'isoformat'):
            summary[k] = v.isoformat()
        elif hasattr(v, '__float__') and not isinstance(v, (int, float)):
            summary[k] = float(v)
    return jsonify(summary)


@app.route('/public-goods-feed', methods=['GET'])
def public_goods_feed():
    """Recent public goods evaluations — free, no payment required."""
    limit = min(int(request.args.get("limit", 20)), 50)
    try:
        with get_conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("""
                    SELECT
                        id, subject AS wallet, verdict, score,
                        status, created_at, attestation_uid
                    FROM predictions
                    WHERE prediction_type = 'public_goods'
                    ORDER BY created_at DESC
                    LIMIT %s
                """, (limit,))
                rows = [dict(r) for r in cur.fetchall()]
        return jsonify({"evaluations": rows, "count": len(rows)})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/chart-data', methods=['GET'])
def chart_data():
    """
    Time-series data for the predictions vs outcomes chart.
    Returns daily buckets of predictions made and outcomes received.
    """
    try:
        with get_conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:

                # Daily prediction counts by verdict (last 30 days)
                cur.execute("""
                    SELECT
                        DATE(created_at)  AS day,
                        verdict,
                        COUNT(*)          AS count
                    FROM predictions
                    WHERE created_at >= NOW() - INTERVAL '30 days'
                      AND prediction_type = 'token'
                    GROUP BY DATE(created_at), verdict
                    ORDER BY day ASC
                """)
                pred_rows = cur.fetchall()

                # Daily resolution outcomes (last 30 days)
                cur.execute("""
                    SELECT
                        DATE(r.created_at)  AS day,
                        r.outcome,
                        COUNT(*)            AS count,
                        AVG(r.accuracy)     AS avg_accuracy
                    FROM resolutions r
                    WHERE r.created_at >= NOW() - INTERVAL '30 days'
                    GROUP BY DATE(r.created_at), r.outcome
                    ORDER BY day ASC
                """)
                res_rows = cur.fetchall()

                # Cumulative accuracy over time
                cur.execute("""
                    SELECT
                        DATE(r.created_at) AS day,
                        COUNT(*) FILTER (WHERE r.outcome = 'TRUE')    AS correct,
                        COUNT(*) FILTER (WHERE r.outcome = 'FALSE')   AS wrong,
                        COUNT(*) FILTER (WHERE r.outcome = 'PARTIAL') AS partial,
                        COUNT(*)                                       AS total
                    FROM resolutions r
                    GROUP BY DATE(r.created_at)
                    ORDER BY day ASC
                """)
                acc_rows = cur.fetchall()

                # Score distribution buckets
                cur.execute("""
                    SELECT
                        CASE
                            WHEN score < 20 THEN '0-19'
                            WHEN score < 40 THEN '20-39'
                            WHEN score < 60 THEN '40-59'
                            WHEN score < 80 THEN '60-79'
                            ELSE '80-100'
                        END AS bucket,
                        COUNT(*) AS count,
                        verdict
                    FROM predictions
                    WHERE prediction_type = 'token'
                    GROUP BY bucket, verdict
                    ORDER BY bucket, verdict
                """)
                dist_rows = cur.fetchall()

        def fmt(rows):
            return [{
                k: (v.isoformat() if hasattr(v, 'isoformat') else
                    float(v) if hasattr(v, '__float__') and not isinstance(v, int) else v)
                for k, v in row.items()
            } for row in rows]

        return jsonify({
            "predictions_by_day": fmt(pred_rows),
            "outcomes_by_day":    fmt(res_rows),
            "accuracy_by_day":    fmt(acc_rows),
            "score_distribution": fmt(dist_rows),
        })

    except Exception as e:
        log.error(f"/chart-data error: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


@app.route('/predictions', methods=['GET'])
def get_predictions():
    """
    View all predictions with optional status filter.
    Useful for monitoring what the watcher has auto-predicted.

    Query params:
      status=pending|resolved|all  (default: all)
      limit=N                      (default: 20, max: 100)
      verdict=BLESSED|MORTAL|CURSED
    """
    status  = request.args.get('status',  'all').upper()
    limit   = min(int(request.args.get('limit', 20)), 100)
    verdict = request.args.get('verdict', '').upper()

    try:
        with get_conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:

                filters = []
                params  = []

                if status != 'ALL':
                    filters.append("status = %s")
                    params.append(status)

                if verdict:
                    filters.append("verdict = %s")
                    params.append(verdict)

                where = ("WHERE " + " AND ".join(filters)) if filters else ""
                params.append(limit)

                cur.execute(f"""
                    SELECT
                        id,
                        subject         AS token_address,
                        verdict,
                        score,
                        status,
                        created_at,
                        resolve_after,
                        resolved_at,
                        resolution_uid,
                        attestation_uid
                    FROM predictions
                    {where}
                    ORDER BY created_at DESC
                    LIMIT %s
                """, params)

                rows = [dict(r) for r in cur.fetchall()]

                # Summary counts
                cur.execute("""
                    SELECT
                        status,
                        verdict,
                        COUNT(*) as count
                    FROM predictions
                    GROUP BY status, verdict
                    ORDER BY status, verdict
                """)
                summary = [dict(r) for r in cur.fetchall()]

        return jsonify({
            "count":       len(rows),
            "summary":     summary,
            "predictions": rows,
        })

    except Exception as e:
        log.error(f"/predictions error: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


@app.route('/reputation', methods=['GET'])
def get_reputation():
    """Public trust stats — queryable by any agent before buying signals."""
    agent_id = request.args.get('agent_id', AGENT_ID)
    stats    = get_reputation_stats(agent_id)
    return jsonify(stats)


@app.route('/trust-check', methods=['GET'])
def trust_check():
    """
    Simple yes/no trust gate for agent-to-agent commerce.
    Free endpoint — drives discovery.
    Other agents call this before deciding to pay for full signals.
    """
    stats       = get_reputation_stats(AGENT_ID)
    trust_score = stats.get("trust_score") or 0
    total       = stats.get("total_resolved") or 0

    return jsonify({
        "trusted":       trust_score >= 70 and total >= 5,
        "trust_score":   trust_score,
        "total_resolved": total,
        "agent_id":      AGENT_ID,
        "wallet":        WALLET_ADDRESS,
        "meets_threshold": {
            "min_score":    70,
            "min_resolved": 5,
        }
    })


@app.route('/watch', methods=['GET', 'POST'])
def trigger_watch():
    """
    Manually trigger a watch cycle.
    Fires in a background thread and returns immediately —
    Venice calls take 45s+ per token so the full cycle takes several minutes.
    Check /predictions afterwards to see new results.
    """
    try:
        from watcher import run_watch_cycle as _run_watch_cycle

        def _bg():
            try:
                results = _run_watch_cycle()
                log.info(f"/watch cycle complete: {len(results)} predicted")
            except Exception as e:
                log.error(f"/watch background error: {e}", exc_info=True)

        t = threading.Thread(target=_bg, daemon=True, name="manual-watch")
        t.start()

        return jsonify({
            "status":  "started",
            "message": (
                "Watch cycle started in background. "
                "Check /predictions in ~5 minutes for new results."
            ),
        })

    except Exception as e:
        log.error(f"/watch error: {e}", exc_info=True)
        return jsonify({"error": str(e), "type": type(e).__name__}), 500


@app.route('/resolve', methods=['GET', 'POST'])
def trigger_resolution():
    """Manually trigger a resolution cycle — useful for testing."""
    results = run_resolution_cycle()
    return jsonify({
        "resolved": len(results),
        "results":  results,
    })


@app.route('/health', methods=['GET'])
def health_check():
    stats = get_reputation_stats(AGENT_ID)
    return jsonify({
        "status":      "ok",
        "service":     "Oracle of Base",
        "trust_score": stats.get("trust_score"),
        "resolved":    stats.get("total_resolved"),
        "pending":     stats.get("pending"),
        "endpoints": {
            "free":    ["/health", "/trust-check", "/reputation", "/predictions", "/skills"],
            "$0.01":   ["/prophecy", "/social-prophecy"],
            "$0.05":   ["/combined-prophecy"],
            "skills": {
                "$0.10": "/skills/apprentice/buy",
                "$0.50": "/skills/seer/buy",
                "$2.00": "/skills/prophet/buy",
            },
        },
        "watcher": {
            "interval_seconds": int(os.getenv("WATCH_INTERVAL_SECONDS", "600")),
            "max_token_age_hours": float(os.getenv("MAX_TOKEN_AGE_HOURS", "2")),
            "min_liquidity_usd": float(os.getenv("MIN_LIQUIDITY_USD", "1000")),
        }
    })


# ── Background resolution scheduler ──────────────────────────────────────────

_scheduler_started = False

def start_resolution_scheduler():
    global _scheduler_started
    if _scheduler_started:
        return
    _scheduler_started = True

    def _loop():
        import time
        interval = int(os.getenv("RESOLUTION_POLL_SECONDS", "300"))
        log.info(f"Resolution scheduler started (interval={interval}s)")
        while True:
            try:
                results = run_resolution_cycle()
                if results:
                    log.info(f"Resolution cycle: {len(results)} resolved")
            except Exception as e:
                log.error(f"Resolution cycle error: {e}", exc_info=True)
            # Run fund exit checks on same cadence
            try:
                fund.run_exit_checks()
            except Exception as e:
                log.warning(f"Fund exit check error: {e}")
            time.sleep(interval)

    t = threading.Thread(target=_loop, daemon=True, name="resolution-engine")
    t.start()
    log.info("Resolution engine thread started.")

# Start at import time so gunicorn picks it up
start_resolution_scheduler()


# ── Background watcher ────────────────────────────────────────────────────────

_watcher_started = False

def start_watcher():
    global _watcher_started
    if _watcher_started:
        return
    _watcher_started = True

    def _loop():
        import time as _time
        from watcher import run_watch_cycle as _run_watch_cycle
        interval = int(os.getenv("WATCH_INTERVAL_SECONDS", "600"))
        log.info(f"Watcher thread started (interval={interval}s)")
        # Small initial delay so app finishes booting before first scan
        _time.sleep(30)
        while True:
            try:
                results = _run_watch_cycle()
                if results:
                    log.info(f"Watcher cycle: {len(results)} new predictions")
            except Exception as e:
                log.error(f"Watcher error: {e}", exc_info=True)
            _time.sleep(interval)

    t = threading.Thread(target=_loop, daemon=True, name="watcher")
    t.start()
    log.info("Watcher thread started.")

start_watcher()

# ── x402 Payment Middleware ───────────────────────────────────────────────────
PaymentMiddleware(app, server, routes)

# ── Local dev entry point ─────────────────────────────────────────────────────
if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5001, debug=False)