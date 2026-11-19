// Same catalogue and fit rules as frameworks.py (both read lib/frameworks.json).
const CAT = require("./frameworks.json");
const BY_ID = Object.fromEntries(CAT.frameworks.map((f) => [f.id, f]));

function family(name) {
  for (const [pat, fam] of CAT.families) if (new RegExp(pat, "i").test(name || "")) return fam;
  return String(name || "").trim();
}
function families(names) { return [...new Set((names || []).filter(Boolean).map(family))].sort(); }

function profileText(p) {
  p = p || {};
  return [p.company_name, p.one_line_description, p.buyer_type, ...(p.target_industries || []), ...(p.sensitive_data_types || [])]
    .filter(Boolean).join(" ").toLowerCase();
}
function hit(words, text) {
  for (const w of words) {
    const esc = w.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    if (new RegExp("\\b" + esc + "\\b", "i").test(text)) return w;
  }
  return null;
}
// Why this company needs the framework, or null. Mirrors frameworks.fit().
function fit(fw, profile, held) {
  if (families(held).map((h) => h.toLowerCase()).includes(fw.family.toLowerCase())) return null;
  const kw = hit(fw.fit.keywords, profileText(profile));
  if (!kw) return null;
  if (fw.fit.markets) {
    const mk = hit(fw.fit.markets, ((profile || {}).geographic_markets || []).join(" ").toLowerCase());
    if (!mk) return null;
    return `"${kw}" in what they do, and they sell in ${mk.length <= 4 ? mk.toUpperCase() : mk[0].toUpperCase() + mk.slice(1)}`;
  }
  return `"${kw}" in what they do`;
}

async function loadState(pool) {
  try {
    const r = await pool.query("SELECT value FROM settings WHERE key = 'portfolio'");
    const v = (r.rows[0] && r.rows[0].value) || {};
    return { accepted: (v.accepted || []).filter((i) => BY_ID[i]), dismissed: (v.dismissed || []).filter((i) => BY_ID[i]) };
  } catch (e) { return { accepted: [], dismissed: [] }; }
}

module.exports = { CAT, BY_ID, family, families, fit, loadState };
