const { getPool } = require("../lib/db");

// Feedback repo (ranked the way the writer sees it) and the winning emails.
async function readBody(req) {
  if (req.body && typeof req.body === "object") return req.body;
  if (typeof req.body === "string") return JSON.parse(req.body || "{}");
  const chunks = [];
  for await (const c of req) chunks.push(c);
  return JSON.parse(Buffer.concat(chunks).toString() || "{}");
}

// POST /api/feedback {guidance, scope?, score?, run_id?} adds your feedback to the repo.
// scope: global | role:<role> | industry:<industry>; score 0..1 (default 0.8).
module.exports = async (req, res) => {
  const pool = getPool();
  try {
    if (req.method === "POST") {
      const b = await readBody(req);
      const guidance = typeof b.guidance === "string" ? b.guidance.trim() : "";
      const scope = typeof b.scope === "string" && /^(global|role:[a-z]+|industry:[a-z0-9 &-]+)$/.test(b.scope.trim().toLowerCase())
        ? b.scope.trim().toLowerCase() : "global";
      const score = b.score == null || b.score === "" ? 0.8 : Number(b.score);
      if (guidance.length < 5 || guidance.length > 600) {
        return res.status(400).json({ error: "feedback must be 5-600 characters" });
      }
      if (!(score >= 0 && score <= 1)) return res.status(400).json({ error: "score must be between 0 and 1" });
      const runId = b.run_id ? parseInt(b.run_id, 10) : null;
      const r = await pool.query(
        "INSERT INTO feedback (source, scope, guidance, outcome_score, evidence) VALUES ('human', $1, $2, $3, $4) RETURNING id",
        [scope, guidance, score, JSON.stringify({ via: "dashboard", run_id: runId })]);
      return res.status(200).json({ ok: true, id: r.rows[0].id, scope, score });
    }
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
