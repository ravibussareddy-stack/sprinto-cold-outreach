const { authorized } = require("../lib/auth");
const { getPool } = require("../lib/db");

// Everything the dashboard tiles need, in one call.
// Engagement rate = conversations with any reply (yes or no) / conversations started.
// Booking rate    = meetings booked / conversations with a positive reply.
const PRICE_IN = parseFloat(process.env.GEMINI_PRICE_INPUT_PER_M || "0.30");
const PRICE_OUT = parseFloat(process.env.GEMINI_PRICE_OUTPUT_PER_M || "2.50");

const STAGE_BY_CONTACT = {
  meeting_booked: "Meeting booked", engaged: "Engaged", contacted: "Contacted",
  cold_lead: "Cold lead", non_respondent: "Non-respondent", escalated: "Needs you",
};
const STAGE_BY_DECISION = {
  QUALIFIED: "Qualified", QUALIFIED_FLAGGED: "Qualified", REACTIVATED: "Qualified",
  DORMANT: "Dormant", DISQUALIFIED: "Disqualified", MANUAL_REVIEW: "Manual review",
};

async function safe(pool, sql, fallback) {
  try { return (await pool.query(sql)).rows; } catch (e) { return fallback; }
}

module.exports = async (req, res) => {
  if (!authorized(req)) return res.status(401).json({ error: "unauthorized" });
  const pool = getPool();
  try {
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

    const [quality] = await safe(pool, `
      SELECT count(*)::int AS drafts,
             avg((record->'disagreement'->>'score')::numeric) AS avg_disagreement,
             avg((record->'quality'->>'passed')::numeric / NULLIF((record->'quality'->>'total')::numeric, 0)) AS avg_quality
      FROM runs WHERE record ? 'email'`, [{}]);

    const usage = await safe(pool, `
      SELECT purpose, count(*)::int AS calls, sum(input_tokens)::bigint AS input_tokens,
             sum(output_tokens)::bigint AS output_tokens
      FROM llm_usage GROUP BY purpose ORDER BY sum(input_tokens + output_tokens) DESC`, []);

    const latest = await safe(pool, `
      SELECT DISTINCT ON (domain) domain, decision, status, score, created_at,
             record->'profile'->>'company_name' AS company
      FROM runs ORDER BY domain, created_at DESC`, []);
    const contacts = await safe(pool, `SELECT domain, status FROM contacts`, []);
    const contactByDomain = {};
    contacts.forEach((c) => { contactByDomain[c.domain] = c.status; });

    const pipeline = latest.map((r) => {
      let stage = contactByDomain[r.domain] ? STAGE_BY_CONTACT[contactByDomain[r.domain]] : null;
      if (!stage) stage = (r.status || "").startsWith("NO_POC") ? "Qualified - no contact"
        : (r.status || "").startsWith("ERROR") ? "Error" : (STAGE_BY_DECISION[r.decision] || r.decision || "Researched");
      return { domain: r.domain, company: r.company || r.domain, stage, decision: r.decision,
               fit_score: r.score, last_activity: r.created_at };
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
      quality: { drafts: quality.drafts || 0,
                 avg_disagreement: quality.avg_disagreement == null ? null : Number(quality.avg_disagreement),
                 avg_quality: quality.avg_quality == null ? null : Number(quality.avg_quality) },
      tokens: { input: tin, output: tout, est_cost_usd: cost(tin, tout),
                price_per_m: { input: PRICE_IN, output: PRICE_OUT },
                by_purpose: usage.map((u) => ({ ...u, input_tokens: Number(u.input_tokens),
                  output_tokens: Number(u.output_tokens),
                  est_cost_usd: cost(Number(u.input_tokens), Number(u.output_tokens)) })) },
      pipeline: { companies: pipeline.length, by_stage: stageCounts, rows: pipeline },
      contacts: contacts.length,
      feedback: fb,
    });
  } catch (e) {
    res.status(500).json({ error: "database read failed" });
  }
};
