const { getPool } = require("../lib/db");
const { DEFAULTS, DORMANT_RULES, RANGES, merge, load } = require("../lib/rubric");

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

module.exports = async (req, res) => {
  const pool = getPool();
  const keyRequired = !!process.env.SETTINGS_EDIT_KEY;
  try {
    if (req.method === "POST") {
      if (keyRequired && req.headers["x-edit-key"] !== process.env.SETTINGS_EDIT_KEY) {
        return res.status(403).json({ error: "edit key required to change the rubric" });
      }
      const body = await readBody(req);
      const before = (await load(pool)).rubric;
      if (body.undo) {
        const ch = (await pool.query("SELECT * FROM rubric_changes WHERE id = $1", [parseInt(body.undo, 10)])).rows[0];
        if (!ch) return res.status(404).json({ error: "change not found" });
        if (ch.reverted) return res.status(400).json({ error: "already undone" });
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
