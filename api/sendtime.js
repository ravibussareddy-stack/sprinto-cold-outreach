const { getPool } = require("../lib/db");
const rubric = require("../lib/rubric");

// Best time to send follow-ups: the research default, your own reply rates by day and hour,
// and the window in force (same logic as sendtime.py).
const DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"];
const TZ = process.env.SLOT_TIMEZONE || "Asia/Kolkata";
const SOURCES = [
  { name: "Belkins 2026 B2B study (7.5M+ emails)", url: "https://belkins.io/blog/best-time-to-send-email",
    finding: "Wednesday and Thursday strongest; 8am-noon best window" },
  { name: "Instantly 2026 cold email benchmark", url: "https://instantly.ai/cold-email-benchmark-report-2026",
    finding: "Tuesday and Wednesday peak reply rates; 42% of replies come from follow-ups" },
  { name: "Smartlead reply-timing benchmark", url: "https://www.smartlead.ai/benchmarks/how-long-to-wait-for-cold-email-reply",
    finding: "93.9% of replies arrive within 72 hours, so the first follow-up waits about 3 days" },
];

function localParts(d) {
  const f = new Intl.DateTimeFormat("en-GB", { timeZone: TZ, weekday: "short", hour: "2-digit", hour12: false }).formatToParts(new Date(d));
  const wd = f.find((x) => x.type === "weekday").value.slice(0, 3).toLowerCase();
  return { day: wd, hour: Number(f.find((x) => x.type === "hour").value) % 24 };
}

module.exports = async (req, res) => {
  const pool = getPool();
  try {
    const sw = (await rubric.load(pool)).rubric.send_window;
    let obs = [];
    try {
      obs = (await pool.query(`
        SELECT e.created_at,
               EXISTS (SELECT 1 FROM emails r WHERE r.thread_id = e.thread_id AND r.kind = 'reply' AND r.created_at > e.created_at
                       AND r.created_at < coalesce((SELECT min(n.created_at) FROM emails n WHERE n.thread_id = e.thread_id
                         AND n.created_at > e.created_at AND (n.kind = 'cold' OR n.kind LIKE 'followup%')), 'infinity'::timestamptz)) AS replied
        FROM emails e WHERE e.thread_id IS NOT NULL AND (e.kind = 'cold' OR e.kind LIKE 'followup%')`)).rows;
    } catch (e) { obs = []; }
    const byDay = Object.fromEntries(DAYS.map((d) => [d, { sent: 0, replied: 0 }]));
    const byHour = Array.from({ length: 24 }, () => ({ sent: 0, replied: 0 }));
    let inWin = { sent: 0, replied: 0 }, outWin = { sent: 0, replied: 0 };
    obs.forEach((o) => {
      const { day, hour } = localParts(o.created_at);
      byDay[day].sent++; byHour[hour].sent++;
      if (o.replied) { byDay[day].replied++; byHour[hour].replied++; }
      const bucket = sw.days.includes(day) && hour >= sw.start_hour && hour < sw.end_hour ? inWin : outWin;
      bucket.sent++; if (o.replied) bucket.replied++;
    });
    const replied = obs.filter((o) => o.replied).length;
    let window = { days: sw.days, start_hour: sw.start_hour, end_hour: sw.end_hour, source: sw.mode === "fixed" ? "fixed" : "research" };
    if (sw.mode === "auto" && obs.length >= sw.min_sends_to_learn && replied > 0) {
      const rate = (x) => (x.sent ? x.replied / x.sent : 0);
      const days = DAYS.filter((d) => byDay[d].sent >= 3).sort((a, b) => rate(byDay[b]) - rate(byDay[a])).slice(0, 3);
      let best = null;
      for (let s = 6; s <= 19; s++) {
        const sent = byHour[s].sent + byHour[s + 1].sent + byHour[s + 2].sent;
        const rep = byHour[s].replied + byHour[s + 1].replied + byHour[s + 2].replied;
        if (sent >= 3 && (!best || rep / sent > best.r)) best = { r: rep / sent, s };
      }
      if (days.length && best) window = { days, start_hour: best.s, end_hour: best.s + 3, source: "observed" };
    }
    res.status(200).json({
      timezone: TZ, mode: sw.mode, min_sends_to_learn: sw.min_sends_to_learn, research_window: { days: sw.days, start_hour: sw.start_hour, end_hour: sw.end_hour },
      window, observed: { sends: obs.length, replied, by_day: byDay,
        in_window: inWin, out_of_window: outWin }, sources: SOURCES,
    });
  } catch (e) {
    res.status(500).json({ error: "database read failed" });
  }
};
