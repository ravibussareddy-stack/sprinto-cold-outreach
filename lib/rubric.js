// The qualification rubric, same rules as rubric.py: defaults from rubric_defaults.json,
// overlaid with the valid values saved in Neon (settings table, key "rubric").
const DEFAULTS = require("./rubric_defaults.json");

const DORMANT_RULES = ["any_named_cert", "soc2_and_iso", "off"];
// Allowed range per section (numbers outside are rejected on save).
const RANGES = {
  decision: [0, 200], size: [1, 1000000], stage_points: [0, 100], compliance_points: [0, 100],
  certification_gap: [0, 100], evidence_loop: [0, 10], review: [0, 100], followups: [1, 30],
  send_window: [0, 24], budget: [0, 1000000], auto_tune: [1, 1000],
};
// Field-level ranges where a section's range does not fit.
const FIELD_RANGES = { "send_window.min_sends_to_learn": [5, 1000], "auto_tune.max_step": [1, 20], "auto_tune.min_outcomes": [3, 1000] };
const SEND_MODES = ["auto", "fixed"];
const WEEKDAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"];

function clone(x) { return JSON.parse(JSON.stringify(x)); }

// Returns { rubric, errors }: valid values applied, invalid ones listed and left at the base value.
function merge(base, override) {
  const out = clone(base), errors = [];
  for (const section of Object.keys(base)) {
    const given = (override || {})[section];
    if (!given || typeof given !== "object") continue;
    for (const key of Object.keys(base[section])) {
      if (!(key in given)) continue;
      const def = base[section][key], v = given[key];
      if (Array.isArray(def)) {
        if (Array.isArray(v)) out[section][key] = v.map((x) => String(x).trim().toLowerCase()).filter(Boolean);
        else errors.push(`${section}.${key} must be a list`);
      } else if (typeof def === "number") {
        const n = typeof v === "string" && v.trim() !== "" ? Number(v) : v;
        const [lo, hi] = FIELD_RANGES[`${section}.${key}`] || RANGES[section] || [0, 1e9];
        if (typeof n === "number" && isFinite(n) && n >= lo && n <= hi) out[section][key] = n;
        else errors.push(`${section}.${key} must be a number from ${lo} to ${hi}`);
      } else if (typeof def === "string") {
        if (typeof v === "string" && v.trim()) out[section][key] = v.trim();
        else errors.push(`${section}.${key} must be text`);
      }
    }
  }
  if (!DORMANT_RULES.includes(out.certifications.dormant_rule)) {
    errors.push("certifications.dormant_rule must be one of " + DORMANT_RULES.join(", "));
    out.certifications.dormant_rule = base.certifications.dormant_rule;
  }
  if (out.decision.review_score > out.decision.qualify_score) {
    errors.push("decision.review_score cannot be above decision.qualify_score");
    out.decision = clone(base.decision);
  }
  if (!["auto", "off"].includes(out.auto_tune.mode)) { errors.push("auto_tune.mode must be auto or off"); out.auto_tune.mode = base.auto_tune.mode; }
  const sw = out.send_window;
  if (!SEND_MODES.includes(sw.mode)) { errors.push("send_window.mode must be auto or fixed"); sw.mode = base.send_window.mode; }
  const badDays = sw.days.filter((x) => !WEEKDAYS.includes(x));
  if (badDays.length || !sw.days.length) { errors.push("send_window.days must be weekday names: " + WEEKDAYS.join(", ")); sw.days = clone(base.send_window.days); }
  if (!(sw.start_hour < sw.end_hour)) { errors.push("send_window.start_hour must be before end_hour"); sw.start_hour = base.send_window.start_hour; sw.end_hour = base.send_window.end_hour; }
  if (out.size.min_employees > out.size.max_employees) {
    errors.push("size.min_employees cannot be above size.max_employees");
    out.size = clone(base.size);
  }
  return { rubric: out, errors };
}

async function load(pool) {
  try {
    const r = await pool.query("SELECT value, updated_at FROM settings WHERE key = 'rubric'");
    if (!r.rows[0]) return { rubric: clone(DEFAULTS), updated_at: null, custom: false };
    return { rubric: merge(DEFAULTS, r.rows[0].value).rubric, updated_at: r.rows[0].updated_at, custom: true };
  } catch (e) {
    return { rubric: clone(DEFAULTS), updated_at: null, custom: false };
  }
}

module.exports = { DEFAULTS, DORMANT_RULES, RANGES, SEND_MODES, WEEKDAYS, merge, load };
