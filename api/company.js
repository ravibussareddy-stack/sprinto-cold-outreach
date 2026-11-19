const { getPool } = require("../lib/db");
const rubric = require("../lib/rubric");

// Everything about one company: why it is where it is, the contact, every email sent and
// received, every draft version, meetings, and the next scheduled step.
//   GET /api/company?domain=atlan.com
async function rows(pool, sql, params) {
  try { return (await pool.query(sql, params)).rows; } catch (e) { return []; }
}

module.exports = async (req, res) => {
  const domain = String(req.query.domain || "").trim().toLowerCase();
  if (!/^[a-z0-9.-]+\.[a-z]{2,}$/.test(domain)) return res.status(400).json({ error: "domain required" });
  const pool = getPool();
  try {
    const r = (await rubric.load(pool)).rubric;
    const runs = await rows(pool, `
      SELECT id, created_at, status, decision, score, record->>'source' AS source, (record->>'parent_run_id')::int AS parent_run_id,
             record->'email'->>'subject' AS subject, record->'email'->>'body' AS body,
             (record->'disagreement'->>'score')::numeric AS disagreement, record->'quality'->>'passed' AS q_passed,
             record->'quality'->>'total' AS q_total, record->'changes' AS changes
      FROM runs WHERE domain = $1 ORDER BY created_at DESC`, [domain]);
    if (!runs.length) return res.status(404).json({ error: "no runs for this company" });
    const latestRec = (await rows(pool, "SELECT record FROM runs WHERE domain = $1 ORDER BY created_at DESC LIMIT 1", [domain]))[0].record || {};
    const q = latestRec.qualification || {}, p = latestRec.profile || {};
    const emails = await rows(pool, `SELECT id, thread_id, kind, subject, body, created_at FROM emails WHERE domain = $1 ORDER BY created_at`, [domain]);
    const threads = await rows(pool, `
      SELECT thread_id, subject, status, outcome, replied, sentiment, followups_sent, last_outbound_at, created_at
      FROM threads WHERE domain = $1 ORDER BY created_at DESC`, [domain]);
    const contacts = await rows(pool, `SELECT contact_name, contact_email, title, status, last_outcome, followups_sent, updated_at FROM contacts WHERE domain = $1`, [domain]);
    const meetings = await rows(pool, `SELECT contact, record->>'booked_slot' AS slot, created_at FROM crm_outcomes WHERE domain = $1 AND outcome = 'booked' ORDER BY created_at DESC`, [domain]);
    const reviews = await rows(pool, `SELECT run_id, email_action, note, created_at FROM reviews WHERE domain = $1 ORDER BY created_at DESC`, [domain]);

    // Cold emails sent before email logging existed: take them from the runs (SENT status).
    const loggedSubjects = new Set(emails.filter((e) => e.kind === "cold").map((e) => e.subject));
    const timeline = emails.map((e) => ({ at: e.created_at, kind: e.kind, subject: e.subject, body: e.body, thread_id: e.thread_id }));
    runs.filter((x) => (x.status || "").startsWith("SENT") && x.subject && !loggedSubjects.has(x.subject))
      .forEach((x) => timeline.push({ at: x.created_at, kind: "cold", subject: x.subject, body: x.body, run_id: x.id, from_run: true }));
    timeline.sort((a, b) => new Date(a.at) - new Date(b.at));

    // Next scheduled step for open conversations (same timers as the reply checker).
    const f = r.followups, steps = [f.after_first_days, f.after_second_days, f.after_third_days];
    const open = threads.filter((t) => t.status === "awaiting").map((t) => {
      const n = t.followups_sent || 0, days = n < steps.length ? steps[n] : f.give_up_after_days;
      return { thread_id: t.thread_id, subject: t.subject, next: n < steps.length ? `follow-up ${n + 1}` : "close as no response",
               due: new Date(new Date(t.last_outbound_at).getTime() + days * 86400000), days };
    });

    let news = [];
    try {
      news = (await pool.query(`SELECT title, url, source, published, funding_round, amount FROM news_items
        WHERE matched_domain = $1 ORDER BY coalesce(published, fetched_at) DESC LIMIT 10`, [domain])).rows;
    } catch (e) { news = []; }
    const refreshes = runs.filter((x) => x.source === "refresh").slice(0, 5)
      .map((x) => ({ at: x.created_at, changes: x.changes || [] }));

    res.status(200).json({
      news, refreshes,
      domain, company: p.company_name || domain, decision: q.decision, fit_score: q.score, reason: q.reason,
      score_reasons: q.score_reasons || [], flags: q.flags || [], pages_read: latestRec.pages_read || [],
      certification: q.certification || null,
      firmographics: q.firmographics || null, company_size_hunter: latestRec.company_size_hunter || null,
      facts: { what_they_do: p.one_line_description, funding_stage: p.funding_stage, employees: p.employee_estimate,
               named_customers: p.named_customers || [], certifications: p.certifications_claimed || [],
               industries: p.target_industries || [] },
      contacts, threads, open, timeline, meetings, reviews, send_window: r.send_window,
      drafts: runs.filter((x) => x.subject).map((x) => ({ id: x.id, at: x.created_at, subject: x.subject, status: x.status,
        disagreement: x.disagreement == null ? null : Number(x.disagreement), quality: x.q_total ? `${x.q_passed}/${x.q_total}` : null,
        source: x.source, parent_run_id: x.parent_run_id })),
      runs: runs.length,
    });
  } catch (e) {
    res.status(500).json({ error: "database read failed" });
  }
};
