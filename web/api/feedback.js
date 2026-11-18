const { getPool } = require("../lib/db");

// Feedback repo (ranked the way the writer sees it) and the winning emails.
module.exports = async (req, res) => {
  const pool = getPool();
  try {
    const lessons = await pool.query(`
      SELECT id, source, scope, guidance, outcome_score, active, evidence->>'outcome' AS outcome, created_at
      FROM feedback ORDER BY active DESC, outcome_score DESC, created_at DESC LIMIT 100`);
    const exemplars = await pool.query(`
      SELECT domain, role, subject, outcome, outcome_score, created_at
      FROM exemplars ORDER BY outcome_score DESC, created_at DESC LIMIT 20`);
    res.status(200).json({ lessons: lessons.rows, exemplars: exemplars.rows });
  } catch (e) {
    res.status(500).json({ error: "database read failed" });
  }
};
