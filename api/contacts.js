const { getPool } = require("../lib/db");

// Clientele: every contact and where they stand.
module.exports = async (req, res) => {
  try {
    const r = await getPool().query(`
      SELECT domain, company, contact_name, contact_email, title, status, last_outcome,
             followups_sent, created_at, updated_at
      FROM contacts ORDER BY updated_at DESC LIMIT 300`);
    res.status(200).json(r.rows);
  } catch (e) {
    res.status(500).json({ error: "database read failed" });
  }
};
