const { getPool } = require("../lib/db");

// GET /api/runs          -> newest 200 runs (list view: subject and disagreement score pulled out)
// GET /api/runs?id=28    -> one full run record (draft, disagreement breakdown, pain points, outcome)
module.exports = async (req, res) => {
  const pool = getPool();
  try {
    if (req.query.id !== undefined) {
      const id = parseInt(req.query.id, 10);
      if (!id) return res.status(400).json({ error: "id must be a number" });
      const r = await pool.query("SELECT * FROM runs WHERE id = $1", [id]);
      if (!r.rows[0]) return res.status(404).json({ error: "not found" });
      return res.status(200).json(r.rows[0]);
    }
    const r = await pool.query(`
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
