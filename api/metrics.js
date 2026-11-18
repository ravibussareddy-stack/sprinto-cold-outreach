const { getPool } = require("../lib/db");
const rubric = require("../lib/rubric");

// Everything the dashboard tiles need, in one call.
// Engagement rate = conversations with any reply (yes or no) / conversations started.
// Booking rate    = meetings booked / conversations with a positive reply.
const PRICE_IN = parseFloat(process.env.GEMINI_PRICE_INPUT_PER_M || "0.30");
const PRICE_OUT = parseFloat(process.env.GEMINI_PRICE_OUTPUT_PER_M || "2.50");
// Costs are shown in INR. Assumed rate; set USD_INR in Vercel to the current one.
const USD_INR = parseFloat(process.env.USD_INR || "88");

const STAGE_BY_CONTACT = {
  meeting_booked: "Meeting booked", engaged: "Engaged", contacted: "Contacted",
  cold_lead: "Cold lead", non_respondent: "Non-respondent", escalated: "Needs you",
};
const STAGE_BY_DECISION = {
  QUALIFIED: "Qualified", QUALIFIED_FLAGGED: "Qualified", REACTIVATED: "Qualified",
  DORMANT: "Dormant", DISQUALIFIED: "Disqualified", MANUAL_REVIEW: "Manual review",
};


async function safe(pool, sql, fallback, params) {
  try { return (await pool.query(sql, params || [])).rows; } catch (e) { return fallback; }
}

module.exports = async (req, res) => {
  const pool = getPool();
  try {
    const REVIEW_THRESHOLD = (await rubric.load(pool)).rubric.review.disagreement_threshold;
    const [conv] = await safe(pool, `
      SELECT count(*)::int AS started,
             count(*) FILTER (WHERE replied)::int AS replied,
             count(*) FILTER (WHERE sentiment = 'positive')::int AS positive,
             count(*) FILTER (WHERE sentiment = 'negative')::int AS negative,
             count(*) FILTER (WHERE outcome = 'booked')::int AS booked,
             count(*) FILTER (WHERE outcome = 'non_respondent')::int AS non_respondent,
             count(*) FILTER (WHERE status = 'awaiting')::int AS awaiting,
             coalesce(sum(followups_sent), 0)::int AS followups
      FROM threads WHERE outcome IS DISTINCT FROM 'cancelled_by_user'`, [{}]);

    const emailKinds = await safe(pool, `SELECT kind, count(*)::int AS n FROM emails GROUP BY kind ORDER BY n DESC`, []);

    // Average disagreement: over all drafts, and over the ones that went wrong -
    // above the review threshold, conversation ended badly (declined / no reply), or rewritten by you.
    const [quality] = await safe(pool, `
      SELECT count(*)::int AS drafts,
             avg(score) AS avg_all,
             count(*) FILTER (WHERE score > $1)::int AS flagged_n,
             avg(score) FILTER (WHERE score > $1) AS avg_flagged,
             count(*) FILTER (WHERE bad)::int AS bad_n,
             avg(score) FILTER (WHERE bad) AS avg_bad,
             count(*) FILTER (WHERE rewritten)::int AS rewritten_n,
             avg(score) FILTER (WHERE rewritten) AS avg_rewritten,
             count(*) FILTER (WHERE score > $1 OR bad OR rewritten)::int AS wrong_n,
             avg(score) FILTER (WHERE score > $1 OR bad OR rewritten) AS avg_wrong,
             count(*) FILTER (WHERE score > $1 AND NOT reviewed)::int AS review_queue,
             avg(quality) AS avg_quality
      FROM (
        SELECT (r.record->'disagreement'->>'score')::numeric AS score,
               (r.record->'quality'->>'passed')::numeric / NULLIF((r.record->'quality'->>'total')::numeric, 0) AS quality,
               EXISTS (SELECT 1 FROM threads t WHERE t.thread_id = r.record->>'thread_id'
                       AND t.outcome IN ('not_interested', 'non_respondent')) AS bad,
               EXISTS (SELECT 1 FROM reviews v WHERE v.run_id = r.id AND v.email_action = 'rewritten') AS rewritten,
               EXISTS (SELECT 1 FROM reviews v WHERE v.run_id = r.id) AS reviewed
        FROM runs r WHERE r.record ? 'email' AND r.record->'disagreement' ? 'score'
      ) x`, [{}], [REVIEW_THRESHOLD]);
    const n = (v) => (v == null ? null : Number(v));

    const usage = await safe(pool, `
      SELECT purpose, count(*)::int AS calls, sum(input_tokens)::bigint AS input_tokens,
             sum(output_tokens)::bigint AS output_tokens
      FROM llm_usage GROUP BY purpose ORDER BY sum(input_tokens + output_tokens) DESC`, []);

    const latest = await safe(pool, `
      SELECT DISTINCT ON (domain) domain, decision, status, score, created_at,
             record->'profile'->>'company_name' AS company,
             record->'qualification'->>'reason' AS reason, record->>'error' AS error
      FROM runs ORDER BY domain, created_at DESC`, []);
    const contacts = await safe(pool, `SELECT domain, status FROM contacts`, []);
    const contactByDomain = {};
    contacts.forEach((c) => { contactByDomain[c.domain] = c.status; });

    const pipeline = latest.map((r) => {
      let stage = contactByDomain[r.domain] ? STAGE_BY_CONTACT[contactByDomain[r.domain]] : null;
      if (!stage) stage = (r.status || "").startsWith("NO_POC") ? "Qualified - no contact"
        : (r.status || "").startsWith("ERROR") ? "Error"
        : r.decision === "FAILED" ? "Site unreachable" : (STAGE_BY_DECISION[r.decision] || r.decision || "Researched");
      return { domain: r.domain, company: r.company || r.domain, stage, decision: r.decision,
               fit_score: r.score, last_activity: r.created_at, reason: r.reason || r.error || null };
    });
    const stageCounts = {};
    pipeline.forEach((p) => { stageCounts[p.stage] = (stageCounts[p.stage] || 0) + 1; });

    const [fb] = await safe(pool, `
      SELECT (SELECT count(*) FROM feedback WHERE active)::int AS lessons,
             (SELECT count(*) FROM exemplars)::int AS exemplars`, [{ lessons: 0, exemplars: 0 }]);

    let tin = 0, tout = 0;
    usage.forEach((u) => { tin += Number(u.input_tokens); tout += Number(u.output_tokens); });
    const cost = (i, o) => (i / 1e6) * PRICE_IN + (o / 1e6) * PRICE_OUT;
    const rate = (a, b) => (b ? a / b : null);

    res.status(200).json({
      conversations: conv,
      engagement_rate: rate(conv.replied, conv.started),
      booking_rate: rate(conv.booked, conv.positive),
      emails_by_kind: emailKinds,
      quality: { drafts: quality.drafts || 0, avg_quality: n(quality.avg_quality) },
      disagreement: {
        threshold: REVIEW_THRESHOLD, review_queue: quality.review_queue || 0,
        all: { avg: n(quality.avg_all), n: quality.drafts || 0 },
        wrong: { avg: n(quality.avg_wrong), n: quality.wrong_n || 0 },
        flagged: { avg: n(quality.avg_flagged), n: quality.flagged_n || 0 },
        bad_outcome: { avg: n(quality.avg_bad), n: quality.bad_n || 0 },
        rewritten: { avg: n(quality.avg_rewritten), n: quality.rewritten_n || 0 },
      },
      tokens: { input: tin, output: tout, est_cost_usd: cost(tin, tout), est_cost_inr: cost(tin, tout) * USD_INR,
                usd_inr: USD_INR, price_per_m: { input: PRICE_IN, output: PRICE_OUT },
                by_purpose: usage.map((u) => ({ ...u, input_tokens: Number(u.input_tokens),
                  output_tokens: Number(u.output_tokens),
                  est_cost_usd: cost(Number(u.input_tokens), Number(u.output_tokens)),
                  est_cost_inr: cost(Number(u.input_tokens), Number(u.output_tokens)) * USD_INR })) },
      pipeline: { companies: pipeline.length, by_stage: stageCounts, rows: pipeline },
      contacts: contacts.length,
      feedback: fb,
    });
  } catch (e) {
    res.status(500).json({ error: "database read failed" });
  }
};
