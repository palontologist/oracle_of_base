/**
 * lit_oracle_skill.js
 * --------------------
 * Oracle of Base — Dark Knowledge Skill for Lit Protocol Chipotle TEE
 *
 * This Lit Action seals three layers of proprietary knowledge:
 *
 *   1. SCORING LOGIC    — the exact Venice prompt engineering and signal weights
 *                         that achieve 74% accuracy. Never exposed to callers.
 *
 *   2. CALIBRATION DATA — historical resolved predictions used as few-shot
 *                         examples. Private RAG over the Oracle's track record.
 *
 *   3. DEPLOYER FLAGS   — sensitive deployer reputation data. Known bad actors
 *                         are flagged without exposing the association list.
 *
 * Inputs (passed as params to Lit.Actions.call):
 *   tokenAddress   — Base token address to score (required)
 *   callerWallet   — wallet requesting the score (for attestation)
 *
 * Output (returned to caller, never the internals):
 *   verdict        — BLESSED | MORTAL | CURSED
 *   score          — 0-100
 *   confidence     — LOW | MEDIUM | HIGH
 *   reasoning      — 1-2 sentences from Venice (not the prompt, just the output)
 *   attestation    — TEE-signed proof this ran inside Chipotle
 *
 * What stays sealed inside the TEE:
 *   - Venice API key
 *   - Oracle API key for calibration data
 *   - The exact prompt engineering
 *   - Historical case examples used for few-shot reasoning
 *   - Flagged deployer list
 *   - Signal weight thresholds
 *
 * Deploy to IPFS with: ipfs add lit_oracle_skill.js
 * Reference via CID in Lit Action execution
 */

const go = async () => {

  // ── Validate inputs ─────────────────────────────────────────────────────────
  const { tokenAddress, callerWallet } = params;

  if (!tokenAddress || !tokenAddress.startsWith("0x") || tokenAddress.length !== 42) {
    return Lit.Actions.setResponse({
      response: JSON.stringify({ error: "Invalid token address" })
    });
  }

  // ── Fetch live on-chain signals (public data, happens outside seal) ─────────
  let tokenData = {};
  let deployerData = {};

  try {
    // DexScreener — public market data
    const dexResp = await fetch(
      `https://api.dexscreener.com/latest/dex/tokens/${tokenAddress}`
    );
    if (dexResp.ok) {
      const dex   = await dexResp.json();
      const pairs = dex.pairs || [];
      if (pairs.length > 0) {
        const main    = pairs.sort((a,b) =>
          (parseFloat(b.liquidity?.usd||0)) - (parseFloat(a.liquidity?.usd||0))
        )[0];
        const liq     = parseFloat(main.liquidity?.usd || 0);
        const vol24h  = parseFloat(main.volume?.h24   || 0);
        const fdv     = parseFloat(main.fdv            || 0);
        const buys    = main.txns?.h24?.buys  || 0;
        const sells   = main.txns?.h24?.sells || 0;
        const ageMs   = main.pairCreatedAt    || 0;
        const ageDays = ageMs ? ((Date.now() - ageMs) / (1000 * 86400)).toFixed(1) : null;
        const pc      = main.priceChange      || {};

        tokenData = {
          name:            main.baseToken?.name,
          symbol:          main.baseToken?.symbol,
          liquidity_usd:   liq,
          volume_24h_usd:  vol24h,
          fdv_usd:         fdv,
          buy_pressure:    (buys / Math.max(buys + sells, 1)).toFixed(3),
          price_change_h1: pc.h1,
          price_change_h24:pc.h24,
          age_days:        ageDays,
          pair_count:      pairs.length,
          liq_fdv_ratio:   fdv > 0 ? (liq / fdv).toFixed(4) : null,
        };
      }
    }
  } catch(e) {
    tokenData = { fetch_error: e.message };
  }

  // ── Retrieve sealed calibration cases (private RAG) ─────────────────────────
  // The Oracle API key is an encrypted lit condition — never visible to caller
  let calibrationExamples = [];
  try {
    const oracleResp = await fetch(
      `https://web-production-b386a.up.railway.app/predictions?status=resolved&limit=5`,
      {
        headers: {
          // Sealed API key injected by Lit Actions from encrypted conditions
          "X-Lit-Oracle-Key": LIT_ORACLE_API_KEY  // decrypted inside TEE only
        }
      }
    );
    if (oracleResp.ok) {
      const data = await oracleResp.json();
      const resolved = (data.predictions || []).filter(p =>
        p.outcome && p.verdict && p.score
      ).slice(0, 5);

      // Build few-shot examples — only what Venice needs, not raw data
      calibrationExamples = resolved.map(p => ({
        liq:     p.raw_signals?.token?.liquidity_usd,
        verdict: p.verdict.split(" ")[0],
        score:   p.score,
        outcome: p.outcome,
      }));
    }
  } catch(e) {
    // Calibration unavailable — proceed without few-shot
    calibrationExamples = [];
  }

  // ── Check sealed deployer flag list ─────────────────────────────────────────
  // The deployer flag list is stored encrypted. Only the TEE can read it.
  let deployerFlagged = false;
  let deployerRugRate = null;
  try {
    const depResp = await fetch(
      `https://web-production-b386a.up.railway.app/deployer-signals?token=${tokenAddress}`,
      {
        headers: { "X-Lit-Oracle-Key": LIT_ORACLE_API_KEY }
      }
    );
    if (depResp.ok) {
      const depData = await depResp.json();
      deployerFlagged = depData.rug_rate_pct > 60;
      deployerRugRate = depData.rug_rate_pct;
      deployerData    = {
        history:   depData.deployer_history,
        rug_rate:  depData.rug_rate_pct,
        flagged:   deployerFlagged,
        tokens:    depData.previous_tokens,
        thriving:  depData.thriving_count,
      };
    }
  } catch(e) {
    deployerData = { available: false };
  }

  // ── Sealed Venice prompt — the core dark knowledge ──────────────────────────
  // The VENICE_API_KEY is an encrypted Lit condition. Callers never see:
  //   - the API key
  //   - the prompt engineering
  //   - the signal weight calibration
  //   - the few-shot examples

  const fewShotBlock = calibrationExamples.length > 0
    ? `Historical calibration (${calibrationExamples.length} similar cases from Oracle track record):
${calibrationExamples.map(e =>
  `  liq=$${e.liq?.toLocaleString()} → ${e.verdict} (score=${e.score}) → resolved ${e.outcome}`
).join("\n")}

Use these as calibration anchors. Do not copy them — reason from them.`
    : "";

  const deployerBlock = deployerFlagged
    ? `DEPLOYER FLAG: This deployer wallet is flagged in the Oracle's private registry.
Rug rate: ${deployerRugRate}%. Weight this heavily toward CURSED.`
    : deployerData.available === false
    ? "Deployer history: not available for this token."
    : `Deployer: ${deployerData.history || "unknown"}, ${deployerData.tokens || 0} previous tokens, rug rate ${deployerData.rug_rate || 0}%.`;

  // Sealed scoring prompt — this is the Oracle's proprietary knowledge
  const sealedPrompt = `You are the Oracle of Base scoring engine with a 74% accuracy track record.
Score this token for rug/scam risk. Return ONLY a JSON object.

${fewShotBlock}

SIGNAL WEIGHTS (sealed, proprietary):
- Liquidity depth: 25% weight
- FDV/liquidity ratio (liq_fdv_ratio): 20% weight — below 0.005 is critical
- Buy/sell pressure: 15% weight
- Price action pattern: 15% weight
- Deployer history: 25% weight (elevated when deployer is flagged)

${deployerBlock}

TOKEN SIGNALS:
${JSON.stringify(tokenData, null, 2)}

Score this token and return ONLY this JSON:
{
  "verdict": "BLESSED" | "MORTAL" | "CURSED",
  "score": <integer 0-100>,
  "confidence": "LOW" | "MEDIUM" | "HIGH",
  "reasoning": "<1-2 sentences maximum — what drove the verdict>"
}`;

  // Call Venice inside the sealed TEE
  let veniceResult = null;
  try {
    const veniceResp = await fetch("https://api.venice.ai/api/v1/chat/completions", {
      method: "POST",
      headers: {
        "Authorization": `Bearer ${VENICE_API_KEY}`,  // decrypted inside TEE only
        "Content-Type":  "application/json",
      },
      body: JSON.stringify({
        model:       "qwen3-5-9b",
        messages:    [{ role: "user", content: sealedPrompt }],
        temperature: 0.2,
        max_tokens:  150,
      }),
    });

    if (veniceResp.ok) {
      const vd  = await veniceResp.json();
      let raw   = vd.choices?.[0]?.message?.content?.trim() || "{}";
      if (raw.startsWith("```")) {
        raw = raw.split("```")[1];
        if (raw.startsWith("json")) raw = raw.slice(4);
      }
      veniceResult = JSON.parse(raw);
    }
  } catch(e) {
    veniceResult = {
      verdict:    "UNKNOWN",
      score:      50,
      confidence: "LOW",
      reasoning:  "Venice inference unavailable inside TEE.",
    };
  }

  // ── Build attested response ──────────────────────────────────────────────────
  // The TEE signs this response. Caller gets proof this ran inside Chipotle.
  // They get the verdict. They never get the prompts, keys, or calibration data.

  const verdict    = veniceResult?.verdict    || "UNKNOWN";
  const score      = veniceResult?.score      || 50;
  const confidence = veniceResult?.confidence || "LOW";
  const reasoning  = veniceResult?.reasoning  || "";

  // Sign the result with the Lit PKP for on-chain attestation
  const messageToSign = ethers.utils.solidityKeccak256(
    ["string", "string", "uint256", "uint256"],
    [tokenAddress, verdict, score, Math.floor(Date.now() / 1000)]
  );

  const sig = await Lit.Actions.signEcdsa({
    toSign:     ethers.utils.arrayify(messageToSign),
    publicKey:  pkpPublicKey,
    sigName:    "oracle_attestation",
  });

  Lit.Actions.setResponse({
    response: JSON.stringify({
      token_address: tokenAddress,
      verdict:       verdict,
      score:         score,
      confidence:    confidence,
      reasoning:     reasoning,
      tee_sealed:    true,
      knowledge_moat: [
        "Venice prompt engineering",
        "Historical calibration dataset",
        "Deployer reputation registry",
        "Signal weight thresholds",
      ],
      attestation: {
        signed_by:   "Lit Chipotle TEE",
        signature:   sig,
        timestamp:   Math.floor(Date.now() / 1000),
      }
    })
  });
};

go().catch(e => {
  Lit.Actions.setResponse({
    response: JSON.stringify({ error: e.message })
  });
});
