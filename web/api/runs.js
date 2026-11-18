const { authorized } = require("../lib/auth");
const { getPool } = require("../lib/db");

// Newest 200 pipeline runs, one row per company run, with the draft subject
// and disagreement score pulled out for the list view.
module.exports = async (req, res) => {
  if (!authorized(req)) return res.status(401).json({ error: "unauthorized" });
  try {
    const r = await getPool().query(`
      SELECT id, domain, decision, score, status, created_at,
             record->'disagreement'->>'score' AS disagreement_score,
             record->'email'->>'subject' AS subject
      FROM runs
      ORDER BY created_at DESC
      LIMIT 200`);
    res.status(200).json(r.rows);
  } catch (e) {
    res.status(500).json({ error: "database read failed" });
  }
};
