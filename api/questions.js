const { getPool } = require("../lib/db");

// Questions the system asks you when it is unsure (questions.py writes them).
//   GET  /api/questions                          -> { open: [...], recent: [...], open_count }
//   POST /api/questions { id, answer }           -> answer it (validated per kind)
//   POST /api/questions { id, dismiss: true }    -> not now (the system carries on without it)
//   POST /api/questions { id, reopen: true }     -> ask again
// Answers about a company are used on its next run; a conversation answer is applied straight away.
async function readBody(req) {
  if (req.body && typeof req.body === "object") return req.body;
  if (typeof req.body === "string") return JSON.parse(req.body || "{}");
  const chunks = [];
  for await (const c of req) chunks.push(c);
  return JSON.parse(Buffer.concat(chunks).toString() || "{}");
}
const EMAIL = /^[^@\s]+@[^@\s]+\.[^@\s]+$/;

function validate(q, answer) {
  if (q.kind === "contact") {
    if (!answer || typeof answer !== "object") return { error: "give the contact's first name and email" };
    const c = {};
    for (const k of ["first_name", "last_name", "email", "title"]) {
      if (answer[k] != null && typeof answer[k] !== "string") return { error: `${k} must be text` };
      c[k] = String(answer[k] || "").trim().slice(0, 120);
    }
    if (!c.first_name) return { error: "first name is needed" };
    if (!EMAIL.test(c.email)) return { error: "a valid email is needed" };
    return { value: c };
  }
  const allowed = (q.options || []).map((o) => o.value);
  if (!allowed.includes(answer)) return { error: "answer must be one of: " + allowed.join(", ") };
  return { value: answer };
}

// What the answer will do, in plain words (shown on the card once answered).
function effect(q, value) {
  const d = q.domain || "this company";
  if (q.kind === "saas") return value === "yes" ? `${d} will be scored as B2B SaaS on its next run.` : `${d} will be disqualified on its next run.`;
  if (q.kind === "certs") return {
    soc2: `${d} will be dormant on its next run (you confirmed SOC 2).`, iso: `${d} will be dormant on its next run (you confirmed ISO 27001).`,
    both: `${d} will be dormant on its next run (you confirmed both).`, none: `${d} earns the greenfield points on its next run (you confirmed neither).`,
    unsure: `No change: ${d} stays flagged as unknown.` }[value];
  if (q.kind === "contact") return `${value.first_name} ${value.last_name || ""} (${value.email}) will be used for ${d} on its next run. Emails still go only to our own inbox.`;
  if (q.kind === "conversation") return value === "self" ? "Marked as yours: no more automatic emails in this conversation." : "Closed as a cold lead: no more automatic emails.";
  return "Saved.";
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
      let note = effect(q, value), applied = false;
      if (q.kind === "conversation") {                 // applied now: the reply checker leaves this thread alone
        const thread = q.key.slice("conversation:".length);
        const outcome = value === "self" ? "handled_by_you" : "closed_by_you";
        await pool.query("UPDATE threads SET status = 'done', outcome = $2, updated_at = now() WHERE thread_id = $1", [thread, outcome]);
        await pool.query("UPDATE contacts SET status = $2, updated_at = now() WHERE domain = $1", [q.domain, value === "self" ? "engaged" : "cold_lead"]);
        applied = true;
      }
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
