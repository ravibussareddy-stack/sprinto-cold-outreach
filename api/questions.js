const { getPool } = require("../lib/db");

// Judgement questions: situations the rules do not settle (questions.py raises them, once each).
//   GET  /api/questions                          -> { open: [...], recent: [...], open_count }
//   POST /api/questions { id, answer }           -> answer it (validated per kind)
//   POST /api/questions { id, dismiss: true }    -> not now (the system carries on without it)
//   POST /api/questions { id, reopen: true }     -> ask again
// An answer is a standing ruling: every company is judged with it from its next run.
async function readBody(req) {
  if (req.body && typeof req.body === "object") return req.body;
  if (typeof req.body === "string") return JSON.parse(req.body || "{}");
  const chunks = [];
  for await (const c of req) chunks.push(c);
  return JSON.parse(Buffer.concat(chunks).toString() || "{}");
}
const EMAIL = /^[^@\s]+@[^@\s]+\.[^@\s]+$/;

function validate(q, answer) {
  if (q.kind === "judgment_open") {                  // a new situation: your ruling in your own words
    const t = typeof answer === "string" ? answer.trim() : "";
    if (t.length < 5 || t.length > 500) return { error: "write your ruling in 5-500 characters" };
    return { value: t };
  }
  const allowed = (q.options || []).map((o) => o.value);
  if (!allowed.includes(answer)) return { error: "answer must be one of: " + allowed.join(", ") };
  return { value: answer };
}

// What the ruling will do, in plain words (shown on the card once answered).
function effect(q, value) {
  if (q.kind === "judgment_open") return "Saved as a ruling: applied to every later company with this kind of finding.";
  const o = (q.options || []).find((x) => x.value === value);
  const seen = (q.examples || []).map((e) => e.company || e.domain).filter(Boolean);
  return (o && o.effect ? o.effect : "Saved.") + " Applies to every company from its next run" +
    (seen.length ? ` (seen so far at ${seen.slice(0, 4).join(", ")}${seen.length > 4 ? "…" : ""}).` : ".");
}

module.exports = async (req, res) => {
  const pool = getPool();
  try {
    if (req.method === "POST") {
      const b = await readBody(req);
      const id = parseInt(b.id, 10);
      if (!id) return res.status(400).json({ error: "send {id, answer} or {id, dismiss: true}" });
      const q = (await pool.query("SELECT * FROM questions WHERE id = $1", [id])).rows[0];
      if (!q) return res.status(404).json({ error: "no question with that id" });
      if (b.dismiss) {
        await pool.query("UPDATE questions SET status = 'dismissed', answered_at = now() WHERE id = $1", [id]);
        return res.status(200).json({ ok: true, status: "dismissed" });
      }
      if (b.reopen) {
        await pool.query("UPDATE questions SET status = 'open', answer = NULL, answered_at = NULL, applied_note = NULL, applied_at = NULL WHERE id = $1", [id]);
        return res.status(200).json({ ok: true, status: "open" });
      }
      const { value, error } = validate(q, b.answer);
      if (error) return res.status(400).json({ error });
      const note = effect(q, value), applied = false;
      await pool.query(`UPDATE questions SET status = 'answered', answer = $2, answered_at = now(),
                        applied_note = $3, applied_at = CASE WHEN $4 THEN now() ELSE NULL END WHERE id = $1`,
                       [id, JSON.stringify(value), note, applied]);
      return res.status(200).json({ ok: true, status: "answered", effect: note, applied, domain: q.domain, kind: q.kind });
    }
    let open = [], recent = [];
    try {
      open = (await pool.query("SELECT * FROM questions WHERE status = 'open' ORDER BY created_at DESC LIMIT 100")).rows;
      recent = (await pool.query("SELECT * FROM questions WHERE status <> 'open' ORDER BY answered_at DESC NULLS LAST LIMIT 30")).rows;
    } catch (e) { /* table not created yet: no questions */ }
    res.status(200).json({ open, recent, open_count: open.length });
  } catch (e) {
    res.status(500).json({ error: "database read failed" });
  }
};
