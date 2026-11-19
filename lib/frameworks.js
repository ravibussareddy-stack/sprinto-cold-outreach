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
function titleMarket(m) { return m.length <= 4 ? m.toUpperCase() : m.replace(/\b\w/g, (c) => c.toUpperCase()).replace(/\bEu\b/, "EU"); }
// Same as qualify.ineligible(): acquired, public or too big companies are never re-opened.
function eligible(p, maxEmployees) {
  p = p || {};
  if (p.acquired_by || p.funding_stage === "Acquired" || p.funding_stage === "Public") return false;
  if ((p.employee_estimate || 0) > (maxEmployees || 1000)) return false;
  return true;
}
// Why this company needs the framework, or null. Mirrors frameworks.fit().
function fit(fw, profile, held) {
  if (families(held).map((h) => h.toLowerCase()).includes(fw.family.toLowerCase())) return null;
  const kw = hit(fw.fit.keywords, profileText(profile));
  if (!kw) return null;
  if (fw.fit.markets) {
    const mk = hit(fw.fit.markets, ((profile || {}).geographic_markets || []).join(" ").toLowerCase());
    if (!mk) return null;
    return `"${kw}" in what they do, and they sell in ${titleMarket(mk)}`;
  }
  return `"${kw}" in what they do`;
}

// State from Neon: accepted / dismissed ids, plus frameworks you added yourself (custom).
async function loadState(pool) {
  let v = {};
  try {
    const r = await pool.query("SELECT value FROM settings WHERE key = 'portfolio'");
    v = (r.rows[0] && r.rows[0].value) || {};
  } catch (e) { v = {}; }
  const custom = (v.custom || []).filter((c) => c && c.id && c.fit && (c.fit.keywords || []).length);
  const byId = { ...BY_ID, ...Object.fromEntries(custom.map((c) => [c.id, c])) };
  return { accepted: (v.accepted || []).filter((i) => byId[i]), dismissed: (v.dismissed || []).filter((i) => byId[i]),
           custom, byId };
}

// A framework you add yourself: validated and shaped like a catalogue entry.
function customFramework(input) {
  const errors = [];
  const name = String((input || {}).name || "").trim();
  const list = (x) => (Array.isArray(x) ? x : String(x || "").split(",")).map((s) => String(s).trim().toLowerCase()).filter(Boolean);
  const keywords = list(input.keywords), markets = list(input.markets);
  const kind = ["certification", "regulation", "framework"].includes(input.kind) ? input.kind : "framework";
  if (name.length < 2 || name.length > 80) errors.push("name must be 2-80 characters");
  if (!keywords.length) errors.push("add at least one keyword that describes who needs it (e.g. fintech, ai agents)");
  if (keywords.some((k) => k.length > 40) || keywords.length > 25) errors.push("keywords: up to 25, each under 40 characters");
  const slug = name.toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_|_$/g, "").slice(0, 40);
  return { errors, fw: { id: "custom_" + slug, name, family: name, kind, introduced: new Date().toISOString().slice(0, 10),
    introduced_note: "added by you", source: null, who: String(input.who || "").trim().slice(0, 300) || "Companies matching your keywords.",
    pitch: String(input.pitch || "").trim().slice(0, 200) || `${name} on top of the controls they already run`,
    fit: markets.length ? { keywords, markets } : { keywords }, custom: true } };
}

module.exports = { CAT, BY_ID, family, families, fit, eligible, loadState, customFramework };
