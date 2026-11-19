const { getPool } = require("../lib/db");
const { DEFAULTS, DORMANT_RULES, RANGES, merge, load } = require("../lib/rubric");
const fw = require("../lib/frameworks");

// Filters & rubric.
//   GET  /api/settings                    -> { rubric, defaults, updated_at, custom, edit_key_required }
//   POST /api/settings { rubric }         -> save (invalid values are rejected with a list of errors)
//   POST /api/settings { reset: true }    -> back to the defaults
// If the SETTINGS_EDIT_KEY env var is set, saving needs the header x-edit-key; viewing never does.
async function readBody(req) {
  if (req.body && typeof req.body === "object") return req.body;
  if (typeof req.body === "string") return JSON.parse(req.body || "{}");
  const chunks = [];
  for await (const c of req) chunks.push(c);
  return JSON.parse(Buffer.concat(chunks).toString() || "{}");
}

// Portfolio of frameworks (new certifications). add: re-opens dormant companies that fit it;
// remove: puts the companies it re-opened back to dormant; dismiss / restore: hide or show a suggestion.
async function portfolioAction(pool, action, id) {
  const f = fw.BY_ID[id];
  if (!f) return { status: 400, body: { error: "unknown framework" } };
  if (!["add", "remove", "dismiss", "restore"].includes(action)) return { status: 400, body: { error: "action must be add, remove, dismiss or restore" } };
  const st = await fw.loadState(pool);
  const acc = new Set(st.accepted), dis = new Set(st.dismissed);
  if (action === "add") { acc.add(id); dis.delete(id); }
  if (action === "remove") acc.delete(id);
  if (action === "dismiss") { dis.add(id); acc.delete(id); }
  if (action === "restore") dis.delete(id);
  await pool.query(`INSERT INTO settings (key, value, updated_at) VALUES ('portfolio', $1, now())
                    ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()`,
                   [JSON.stringify({ accepted: [...acc], dismissed: [...dis] })]);
  const changed = [];
  if (action === "add" || action === "remove") {
    const maxEmployees = (await load(pool)).rubric.size.max_employees;
    const latest = (await pool.query(`SELECT DISTINCT ON (domain) id, domain, decision, record FROM runs
                                      ORDER BY domain, created_at DESC`)).rows;
    for (const r of latest) {
      const rec = r.record || {}, q = rec.qualification || {}, p = rec.profile || {};
      if (action === "add" && r.decision === "DORMANT" && fw.eligible(p, maxEmployees)) {
        const why = fw.fit(f, p, q.certs_held || []);
        if (!why) continue;
        const reason = `Re-opened: already certified (${(q.certs_held || []).join(", ")}), but needs ${f.name} (${why}), now in your portfolio. Run it again to research and draft.`;
        const out = { domain: r.domain, status: "REACTIVATED", source: "portfolio", parent_run_id: r.id, profile: p,
                      pages_read: rec.pages_read || [], qualification: { decision: "REACTIVATED", reason, certs_held: q.certs_held || [],
                      certification: q.certification || null, expansion: [`${f.name} (${why})`], flags: [`expansion prospect: pitch ${f.name}`] } };
        await pool.query("INSERT INTO runs (domain, decision, score, status, record) VALUES ($1, 'REACTIVATED', NULL, 'REACTIVATED', $2)",
                         [r.domain, JSON.stringify(out)]);
        changed.push({ domain: r.domain, company: p.company_name || r.domain, reason: why });
      }
      if (action === "remove" && r.decision === "REACTIVATED" && rec.source === "portfolio" &&
          (q.expansion || []).some((e) => e.startsWith(f.name))) {
        const out = { ...rec, status: "DORMANT", parent_run_id: r.id,
                      qualification: { ...q, decision: "DORMANT", reason: `Back to dormant: ${f.name} was removed from your portfolio.`, expansion: [] } };
        await pool.query("INSERT INTO runs (domain, decision, score, status, record) VALUES ($1, 'DORMANT', NULL, 'DORMANT', $2)",
                         [r.domain, JSON.stringify(out)]);
        changed.push({ domain: r.domain, company: p.company_name || r.domain });
      }
    }
    const words = action === "add"
      ? `You added ${f.name} to the portfolio. ${changed.length ? changed.length + " dormant compan" + (changed.length > 1 ? "ies were" : "y was") + " re-opened: " + changed.map((c) => c.company).join(", ") + "." : "No dormant company fits it yet."} New companies that fit it now qualify as expansion prospects.`
      : `You removed ${f.name} from the portfolio.${changed.length ? " " + changed.length + " re-opened compan" + (changed.length > 1 ? "ies went" : "y went") + " back to dormant." : ""}`;
    await pool.query(`INSERT INTO rubric_changes (section, key, old_value, new_value, reason, source)
                      VALUES ('portfolio', $1, $2, $3, $4, 'you')`,
      [id, JSON.stringify(action === "add" ? "not in portfolio" : "in portfolio"),
       JSON.stringify(action === "add" ? "in portfolio" : "not in portfolio"), words]);
  }
  return { status: 200, body: { ok: true, action, id, name: f.name, changed } };
}

module.exports = async (req, res) => {
  const pool = getPool();
  const keyRequired = !!process.env.SETTINGS_EDIT_KEY;
  try {
    if (req.method === "POST") {
      if (keyRequired && req.headers["x-edit-key"] !== process.env.SETTINGS_EDIT_KEY) {
        return res.status(403).json({ error: "edit key required to change the rubric" });
      }
      const body = await readBody(req);
      if (body.portfolio) {
        const r = await portfolioAction(pool, body.portfolio.action, body.portfolio.id);
        return res.status(r.status).json(r.body);
      }
      const before = (await load(pool)).rubric;
      if (body.undo) {
        const ch = (await pool.query("SELECT * FROM rubric_changes WHERE id = $1", [parseInt(body.undo, 10)])).rows[0];
        if (!ch) return res.status(404).json({ error: "change not found" });
        if (ch.reverted) return res.status(400).json({ error: "already undone" });
        if (ch.section === "portfolio") {                    // undo = the opposite portfolio action
          const added = JSON.stringify(ch.new_value).includes("in portfolio") && !JSON.stringify(ch.new_value).includes("not in");
          const r = await portfolioAction(pool, added ? "remove" : "add", ch.key);
          await pool.query("UPDATE rubric_changes SET reverted = true WHERE id = $1", [ch.id]);
          return res.status(r.status).json(r.body);
        }
        const next = JSON.parse(JSON.stringify(before));
        next[ch.section][ch.key] = ch.old_value;
        const merged = merge(DEFAULTS, next);
        if (merged.errors.length) return res.status(400).json({ error: "cannot undo", errors: merged.errors });
        await pool.query(`INSERT INTO settings (key, value, updated_at) VALUES ('rubric', $1, now())
                          ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()`, [JSON.stringify(merged.rubric)]);
        await pool.query("UPDATE rubric_changes SET reverted = true WHERE id = $1", [ch.id]);
        await pool.query(`INSERT INTO rubric_changes (section, key, old_value, new_value, reason, source)
                          VALUES ($1, $2, $3, $4, $5, 'undo')`,
          [ch.section, ch.key, JSON.stringify(ch.new_value), JSON.stringify(ch.old_value),
           `You undid change #${ch.id}. The tuner will leave this setting alone for 14 days.`]);
        return res.status(200).json({ ok: true });
      }
      if (body.reset) {
        await pool.query("DELETE FROM settings WHERE key = 'rubric'");
        return res.status(200).json({ ok: true, rubric: DEFAULTS, custom: false });
      }
      const { rubric, errors } = merge(DEFAULTS, body.rubric);
      if (errors.length) return res.status(400).json({ error: "some values were not valid", errors });
      for (const section of Object.keys(rubric)) {           // log what you changed
        for (const key of Object.keys(rubric[section])) {
          if (JSON.stringify(rubric[section][key]) !== JSON.stringify(before[section][key])) {
            await pool.query(`INSERT INTO rubric_changes (section, key, old_value, new_value, reason, source)
                              VALUES ($1, $2, $3, $4, 'Changed by you on the Filters & rubric page.', 'you')`,
              [section, key, JSON.stringify(before[section][key]), JSON.stringify(rubric[section][key])]);
          }
        }
      }
      await pool.query(
        `INSERT INTO settings (key, value, updated_at) VALUES ('rubric', $1, now())
         ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()`, [JSON.stringify(rubric)]);
      return res.status(200).json({ ok: true, rubric, custom: true });
    }
    const current = await load(pool);
    let changes = [], tuning = null;
    try {
      changes = (await pool.query(`SELECT id, section, key, old_value, new_value, reason, evidence, source, reverted, created_at
                                   FROM rubric_changes ORDER BY created_at DESC LIMIT 60`)).rows;
      tuning = ((await pool.query("SELECT value, updated_at FROM settings WHERE key = 'tuning_status'")).rows[0] || {}).value || null;
    } catch (e) { /* tables appear on the first Python run */ }
    res.status(200).json({ ...current, changes, tuning, defaults: DEFAULTS, dormant_rules: DORMANT_RULES, ranges: RANGES,
                           edit_key_required: keyRequired });
  } catch (e) {
    res.status(500).json({ error: "database request failed" });
  }
};
