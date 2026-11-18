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
      if (body.reset) {
        await pool.query("DELETE FROM settings WHERE key = 'rubric'");
        return res.status(200).json({ ok: true, rubric: DEFAULTS, custom: false });
      }
      const { rubric, errors } = merge(DEFAULTS, body.rubric);
      if (errors.length) return res.status(400).json({ error: "some values were not valid", errors });
      await pool.query(
        `INSERT INTO settings (key, value, updated_at) VALUES ('rubric', $1, now())
         ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()`, [JSON.stringify(rubric)]);
      return res.status(200).json({ ok: true, rubric, custom: true });
    }
    const current = await load(pool);
    res.status(200).json({ ...current, defaults: DEFAULTS, dormant_rules: DORMANT_RULES, ranges: RANGES,
                           edit_key_required: keyRequired });
  } catch (e) {
    res.status(500).json({ error: "database request failed" });
  }
};
