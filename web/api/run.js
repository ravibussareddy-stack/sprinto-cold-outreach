const { authorized } = require("../lib/auth");
const { getPool } = require("../lib/db");

// One full run record: draft, disagreement breakdown, pain points, outcome.
module.exports = async (req, res) => {
  if (!authorized(req)) return res.status(401).json({ error: "unauthorized" });
  const id = parseInt(req.query.id, 10);
  if (!id) return res.status(400).json({ error: "id required" });
  try {
    const r = await getPool().query("SELECT * FROM runs WHERE id = $1", [id]);
    if (!r.rows[0]) return res.status(404).json({ error: "not found" });
    res.status(200).json(r.rows[0]);
  } catch (e) {
    res.status(500).json({ error: "database read failed" });
  }
};
