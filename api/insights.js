const { getPool } = require("../lib/db");
const rubric = require("../lib/rubric");

// The Overview page: what needs doing now, and how this week compares with last week.
const PRICE_IN = parseFloat(process.env.GEMINI_PRICE_INPUT_PER_M || "0.30");
const PRICE_OUT = parseFloat(process.env.GEMINI_PRICE_OUTPUT_PER_M || "2.50");
const USD_INR = parseFloat(process.env.USD_INR || "88");

async function rows(pool, sql, params) {
  try { return (await pool.query(sql, params || [])).rows; } catch (e) { return []; }
}
async function one(pool, sql, params) { return (await rows(pool, sql, params))[0] || {}; }
const num = (v) => (v == null ? null : Number(v));

// Same numbers for two windows: this week (days 0-7 back) and last week (7-14 back).
async function windowStats(pool, fromDays, toDays, threshold) {
  const p = [fromDays, toDays];
  const runs = await one(pool, `
    SELECT count(DISTINCT domain)::int AS companies,
           count(DISTINCT domain) FILTER (WHERE decision IN ('QUALIFIED','QUALIFIED_FLAGGED','REACTIVATED'))::int AS qualified,
           count(*) FILTER (WHERE record ? 'email')::int AS drafts,
           avg((record->'disagreement'->>'score')::numeric) AS avg_disagreement,
           count(*) FILTER (WHERE (record->'disagreement'->>'score')::numeric > $3)::int AS above_threshold,
           avg((record->'quality'->>'passed')::numeric / NULLIF((record->'quality'->>'total')::numeric, 0)) AS avg_quality
    FROM runs WHERE created_at > now() - make_interval(days => $1) AND created_at <= now() - make_interval(days => $2)`,
    [...p, threshold]);
  const conv = await one(pool, `
    SELECT count(*)::int AS started, count(*) FILTER (WHERE replied)::int AS replied,
           count(*) FILTER (WHERE sentiment = 'positive')::int AS positive,
           count(*) FILTER (WHERE outcome = 'booked')::int AS booked
    FROM threads WHERE outcome IS DISTINCT FROM 'cancelled_by_user'
      AND created_at > now() - make_interval(days => $1) AND created_at <= now() - make_interval(days => $2)`, p);
  const sent = await one(pool, `
    SELECT count(*) FILTER (WHERE kind <> 'reply')::int AS emails_sent, count(*) FILTER (WHERE kind = 'reply')::int AS replies
    FROM emails WHERE created_at > now() - make_interval(days => $1) AND created_at <= now() - make_interval(days => $2)`, p);
  const tok = await one(pool, `
    SELECT coalesce(sum(input_tokens),0)::bigint AS tin, coalesce(sum(output_tokens),0)::bigint AS tout
    FROM llm_usage WHERE created_at > now() - make_interval(days => $1) AND created_at <= now() - make_interval(days => $2)`, p);
  return {
    companies: runs.companies || 0, qualified: runs.qualified || 0, drafts: runs.drafts || 0,
    avg_disagreement: num(runs.avg_disagreement), above_threshold: runs.above_threshold || 0,
    avg_quality: num(runs.avg_quality),
    conversations: conv.started || 0, replied: conv.replied || 0, positive: conv.positive || 0, booked: conv.booked || 0,
    engagement_rate: conv.started ? conv.replied / conv.started : null,
    booking_rate: conv.positive ? conv.booked / conv.positive : null,
    emails_sent: sent.emails_sent || 0, replies: sent.replies || 0,
    tokens: Number(tok.tin || 0) + Number(tok.tout || 0),
    est_cost_usd: (Number(tok.tin || 0) / 1e6) * PRICE_IN + (Number(tok.tout || 0) / 1e6) * PRICE_OUT,
    est_cost_inr: ((Number(tok.tin || 0) / 1e6) * PRICE_IN + (Number(tok.tout || 0) / 1e6) * PRICE_OUT) * USD_INR,
  };
}

module.exports = async (req, res) => {
  const pool = getPool();
  try {
    const r = (await rubric.load(pool)).rubric;
    const threshold = r.review.disagreement_threshold;

    // ---------- to-do ----------
    const latest = await rows(pool, `
      SELECT DISTINCT ON (domain) domain, decision, status, record->'qualification'->>'reason' AS reason,
             record->'profile'->>'company_name' AS company, created_at
      FROM runs ORDER BY domain, created_at DESC`);
    const review = await one(pool, `
      SELECT count(*)::int AS n FROM runs r
      WHERE (r.record->'disagreement'->>'score')::numeric > $1 AND NOT EXISTS (SELECT 1 FROM reviews v WHERE v.run_id = r.id)`, [threshold]);
    const awaiting = await one(pool, `
      SELECT count(*)::int AS n,
             count(*) FILTER (WHERE last_outbound_at < now() - interval '48 hours')::int AS due,
             min(created_at) AS oldest
      FROM threads WHERE status = 'awaiting'`);
    const escalated = await rows(pool, `SELECT domain, contact_name, last_outcome FROM contacts WHERE status = 'escalated' ORDER BY updated_at DESC`);
    const meetings = await rows(pool, `
      SELECT DISTINCT ON (domain) domain, contact, record->>'booked_slot' AS slot, created_at
      FROM crm_outcomes WHERE outcome = 'booked' ORDER BY domain, created_at DESC`);
    const upcoming = meetings.filter((m) => m.slot && new Date(m.slot) > new Date())
      .sort((a, b) => new Date(a.slot) - new Date(b.slot));

    const pick = (fn) => latest.filter(fn);
    const noContact = pick((x) => (x.status || "").startsWith("NO_POC"));
    const manual = pick((x) => x.decision === "MANUAL_REVIEW");
    const failed = pick((x) => x.decision === "FAILED" || (x.status || "").startsWith("ERROR"));
    const dormant = pick((x) => x.decision === "DORMANT");

    const todo = [];
    if (review.n) todo.push({ severity: "high", title: `Review ${review.n} draft(s) above disagreement ${threshold}`,
      detail: "Mark each one Kept or Rewritten.", link: "#review" });
    if (escalated.length) todo.push({ severity: "high", title: `${escalated.length} conversation(s) handed to you`,
      detail: escalated.slice(0, 3).map((e) => `${e.contact_name || e.domain} (${e.last_outcome || "needs a reply"})`).join(", "), link: "#contacts" });
    if (noContact.length) todo.push({ severity: "medium", title: `Add a contact for ${noContact.length} qualified compan${noContact.length > 1 ? "ies" : "y"}`,
      detail: noContact.slice(0, 4).map((x) => x.domain).join(", ") + " - qualified but no decision-maker found (add to POC_OVERRIDES).", link: "#pipeline" });
    if (manual.length) todo.push({ severity: "medium", title: `${manual.length} compan${manual.length > 1 ? "ies" : "y"} need a manual check`,
      detail: manual.slice(0, 4).map((x) => x.domain).join(", ") + " - could not tell whether it is B2B SaaS.", link: "#pipeline" });
    if (awaiting.n) todo.push({ severity: "low", title: `${awaiting.n} conversation(s) waiting for a reply`,
      detail: awaiting.due ? `${awaiting.due} will get a follow-up at the next reply check.` : "No follow-ups due yet.", link: "#contacts" });
    if (upcoming.length) todo.push({ severity: "low", title: `${upcoming.length} upcoming meeting(s)`,
      detail: upcoming.slice(0, 3).map((m) => `${m.contact || m.domain}: ${new Date(m.slot).toUTCString().slice(0, 22)} UTC`).join("; "), link: "#contacts" });
    if (failed.length) todo.push({ severity: "low", title: `${failed.length} compan${failed.length > 1 ? "ies" : "y"} could not be processed`,
      detail: failed.slice(0, 4).map((x) => `${x.domain}: ${(x.reason || x.status || "").slice(0, 60)}`).join("; "), link: "#pipeline" });
    const month = await one(pool, `SELECT coalesce(sum(input_tokens),0)::bigint AS tin, coalesce(sum(output_tokens),0)::bigint AS tout
                                   FROM llm_usage WHERE created_at >= date_trunc('month', now())`);
    const spentMonth = ((Number(month.tin || 0) / 1e6) * PRICE_IN + (Number(month.tout || 0) / 1e6) * PRICE_OUT) * USD_INR;
    const limit = Number(r.budget.monthly_inr) || 0;
    if (limit && spentMonth >= limit) todo.unshift({ severity: "high", title: "Paused: monthly budget reached",
      detail: `Spent \u20b9${spentMonth.toFixed(2)} of \u20b9${limit}. Raise the budget on Filters & rubric to resume.`, link: "settings.html" });
    else if (limit && spentMonth >= 0.8 * limit) todo.push({ severity: "medium", title: `${Math.round(spentMonth / limit * 100)}% of this month's budget used`,
      detail: `\u20b9${spentMonth.toFixed(2)} of \u20b9${limit}; everything pauses at the limit.`, link: "index.html#costs" });
    if (latest.length && dormant.length / latest.length > 0.5) todo.push({ severity: "low",
      title: `${dormant.length} of ${latest.length} companies are dormant`,
      detail: "Most already hold SOC 2. If SOC 2-only companies should be expansion leads, change the dormant rule.",
      link: "settings.html" });

    // ---------- weekly ----------
    const cur = await windowStats(pool, 7, 0, threshold);
    const prev = await windowStats(pool, 14, 7, threshold);
    const METRICS = [
      ["companies", "Companies researched", "count", true], ["qualified", "Qualified", "count", true],
      ["emails_sent", "Emails sent", "count", true], ["replies", "Replies received", "count", true],
      ["engagement_rate", "Engagement rate", "pct", true], ["booked", "Meetings booked", "count", true],
      ["booking_rate", "Booking rate", "pct", true], ["avg_quality", "Email quality", "pct", true],
      ["avg_disagreement", "Avg disagreement", "score", false], ["est_cost_inr", "Model cost", "inr", false],
    ];
    const trends = METRICS.map(([key, label, fmt, higherBetter]) => {
      const a = cur[key], b = prev[key];
      const delta = a == null || b == null ? null : a - b;
      return { key, label, fmt, current: a, previous: b, delta,
               better: delta == null || delta === 0 ? null : (delta > 0) === higherBetter };
    });

    // What drove the week: why companies stopped, which checks failed most.
    const certCounts = {};
    dormant.forEach((x) => {
      const m = (x.reason || "").match(/\(([^)]+)\)/);
      (m ? m[1].split(",") : []).forEach((c) => { const k = c.trim(); if (k) certCounts[k] = (certCounts[k] || 0) + 1; });
    });
    const failedChecks = await rows(pool, `
      SELECT f AS check_name, count(*)::int AS n
      FROM runs, jsonb_array_elements_text(coalesce(record->'quality'->'failed', '[]'::jsonb)) f
      WHERE created_at > now() - interval '7 days' GROUP BY f ORDER BY n DESC LIMIT 5`);
    const topLessons = await rows(pool, `
      SELECT guidance, outcome_score FROM feedback WHERE active AND created_at > now() - interval '7 days'
      ORDER BY outcome_score DESC, created_at DESC LIMIT 3`);

    const highlights = [];
    const pct = (x) => Math.round(x * 100) + "%";
    highlights.push(`${cur.companies} compan${cur.companies === 1 ? "y" : "ies"} researched this week, ${cur.qualified} qualified` +
      (prev.companies ? ` (last week: ${prev.companies} researched, ${prev.qualified} qualified).` : "."));
    if (cur.conversations) highlights.push(`Engagement ${cur.engagement_rate == null ? "-" : pct(cur.engagement_rate)} across ${cur.conversations} conversation(s); ` +
      `${cur.booked} meeting(s) booked from ${cur.positive} positive repl${cur.positive === 1 ? "y" : "ies"}.`);
    if (cur.drafts) highlights.push(`${cur.drafts} draft(s): average disagreement ${cur.avg_disagreement == null ? "-" : Math.round(cur.avg_disagreement)}/100, ` +
      `${cur.above_threshold} above ${threshold}` + (cur.avg_quality == null ? "." : `, ${pct(cur.avg_quality)} of quality checks passed.`));
    const certTop = Object.entries(certCounts).sort((a, b) => b[1] - a[1]);
    if (certTop.length) highlights.push(`Dormant companies most often hold ${certTop.slice(0, 3).map(([c, n]) => `${c} (${n})`).join(", ")}.`);
    if (failedChecks.length) highlights.push(`Most-failed quality check: ${failedChecks[0].check_name} (${failedChecks[0].n} draft(s)).`);
    if (cur.tokens) highlights.push(`Model cost about ₹${cur.est_cost_inr.toFixed(2)} (${cur.tokens.toLocaleString("en-IN")} tokens).`);

    res.status(200).json({ todo, trends, highlights, dormant_certs: certTop.map(([cert, n]) => ({ cert, n })),
                           failed_checks: failedChecks, top_lessons: topLessons, this_week: cur, last_week: prev });
  } catch (e) {
    res.status(500).json({ error: "database read failed" });
  }
};
