const { getPool } = require("../lib/db");
const rubric = require("../lib/rubric");

// Review queue: drafts whose disagreement score is above the rubric's review threshold (default 20)
// and that nobody has reviewed yet.
//   GET  /api/review                      -> the queue
//   POST /api/review {run_id, action}     -> action: "kept" | "rewritten"; leaves the queue
// A review is stored in the reviews table, which also feeds the rewrite rate (review.py metrics).

async function readBody(req) {
  if (req.body && typeof req.body === "object") return req.body;
  if (typeof req.body === "string") return JSON.parse(req.body || "{}");
  const chunks = [];
  for await (const c of req) chunks.push(c);
  return JSON.parse(Buffer.concat(chunks).toString() || "{}");
}

module.exports = async (req, res) => {
  const pool = getPool();
  try {
    const THRESHOLD = (await rubric.load(pool)).rubric.review.disagreement_threshold;
    if (req.method === "POST") {
      const body = await readBody(req);
      const runId = parseInt(body.run_id, 10);
      if (!runId || !["kept", "rewritten"].includes(body.action)) {
        return res.status(400).json({ error: "run_id and action (kept | rewritten) required" });
      }
      const run = await pool.query("SELECT domain FROM runs WHERE id = $1", [runId]);
      if (!run.rows[0]) return res.status(404).json({ error: "run not found" });
      await pool.query(
        "INSERT INTO reviews (run_id, domain, email_action, note) VALUES ($1, $2, $3, $4)",
        [runId, run.rows[0].domain, body.action, "reviewed on the dashboard (disagreement above threshold)"]);
      return res.status(200).json({ ok: true });
    }
    const r = await pool.query(`
      SELECT r.id, r.domain, r.created_at, r.status,
             (r.record->'disagreement'->>'score')::numeric AS score,
             r.record->'disagreement'->'parts' AS parts,
             r.record->'email'->>'subject' AS subject,
             r.record->'quality'->'failed' AS failed_checks
      FROM runs r
      WHERE (r.record->'disagreement'->>'score')::numeric > $1
        AND NOT EXISTS (SELECT 1 FROM reviews v WHERE v.run_id = r.id)
      ORDER BY r.created_at DESC LIMIT 100`, [THRESHOLD]);
    res.status(200).json({ threshold: THRESHOLD, items: r.rows });
  } catch (e) {
    res.status(500).json({ error: "database request failed" });
  }
};
