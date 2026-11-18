// Shared helpers for the dashboard pages (no login: anyone with the link can view).
(function () {
  async function api(path, options) {
    const res = await fetch(path, options);
    if (!res.ok) throw new Error("Request failed (" + res.status + ")");
    return res.json();
  }

  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  function pct(x) { return x == null ? "-" : Math.round(x * 100) + "%"; }
  function num(n) { return n == null ? "-" : Number(n).toLocaleString("en-GB"); }
  function when(iso) {
    if (!iso) return "-";
    const d = new Date(iso);
    return d.toLocaleDateString("en-GB", { day: "2-digit", month: "short" }) + " " +
           d.toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" });
  }

  // Animate a number from 0 to its value (respects reduced motion).
  function countUp(el, target, format) {
    const reduce = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    if (target == null || isNaN(target) || reduce) { el.textContent = format(target); return; }
    const start = Date.now(), dur = 700;
    (function step() {
      const k = Math.min(1, (Date.now() - start) / dur), e = 1 - Math.pow(1 - k, 3);
      el.textContent = format(k < 1 ? target * e : target);
      if (k < 1) setTimeout(step, 16);
    })();
  }

  // Cursor-following highlight on every glass surface.
  document.addEventListener("pointermove", function (ev) {
    const el = ev.target.closest && ev.target.closest(".glass");
    if (!el) return;
    const r = el.getBoundingClientRect();
    el.style.setProperty("--mx", (ev.clientX - r.left) + "px");
    el.style.setProperty("--my", (ev.clientY - r.top) + "px");
  });

  // Navigation shared by every page, in groups. Dashboard sections are hash pages in index.html.
  var NAV = [
    ["", [["overview", "Overview", "index.html#overview"]]],
    ["Outreach", [["pipeline", "Pipeline", "index.html#pipeline"], ["contacts", "Contacts", "index.html#contacts"],
                  ["emails", "Emails", "index.html#emails"]]],
    ["Quality", [["review", "Review", "index.html#review"], ["learning", "Feedback & learning", "index.html#learning"]]],
    ["Setup", [["costs", "Costs", "index.html#costs"], ["rubric", "Filters & rubric", "settings.html"],
               ["test", "Run a test case", "test.html"], ["how", "How it works", "how.html"]]],
  ];
  var badges = {};
  function nav(active) {
    var el = document.getElementById("nav");
    if (!el) return;
    el.innerHTML = NAV.map(function (g) {
      return (g[0] ? '<span class="group">' + esc(g[0]) + "</span>" : "") + g[1].map(function (n) {
        var badge = badges[n[0]] ? '<span class="badge">' + badges[n[0]] + "</span>" : "";
        return '<a href="' + n[2] + '" class="' + (n[0] === active ? "on" : "") + '" data-key="' + n[0] + '">' + esc(n[1]) + badge + "</a>";
      }).join("");
    }).join("");
  }
  function navBadge(key, n) {
    badges[key] = n || 0;
    var a = document.querySelector('#nav a[data-key="' + key + '"]');
    if (!a) return;
    var b = a.querySelector(".badge");
    if (!n) { if (b) b.remove(); return; }
    if (!b) { b = document.createElement("span"); b.className = "badge"; a.appendChild(b); }
    b.textContent = n;
  }

  // "SENT | no_reply (flagged: thin evidence, ...)" -> short pills: Sent, No reply, Flagged
  var STATUS_WORDS = { SENT: ["Sent", "strong"], DRAFTED: ["Draft", ""], REDRAFTED: ["Redraft", "mid"],
    DRAFT_FAILED_CHECKS: ["Failed checks", "warn"], NO_POC: ["No contact", "soft"], DORMANT: ["Dormant", "soft"],
    DISQUALIFIED: ["Disqualified", "soft"], MANUAL_REVIEW: ["Manual check", "warn"], FAILED: ["Unreachable", "warn"],
    ERROR: ["Error", "warn"], awaiting_reply: ["Awaiting reply", ""], no_reply: ["No reply", "soft"],
    booked: ["Booked", "strong"], not_interested: ["Declined", "soft"], non_respondent: ["No response", "soft"],
    escalated_to_growth_lead: ["Needs you", "warn"], aborted: ["Aborted", "warn"] };
  function statusPills(status) {
    if (!status) return "";
    var parts = status.split(" (")[0].split("|").map(function (x) { return x.trim(); }).filter(Boolean);
    var html = parts.map(function (p) {
      var w = STATUS_WORDS[p] || [p.replace(/_/g, " ").toLowerCase(), "soft"];
      return '<span class="pill ' + w[1] + '">' + esc(w[0]) + "</span>";
    }).join(" ");
    if (status.indexOf("flagged") >= 0) html += ' <span class="pill warn" title="thin evidence: review before a real send">Flagged</span>';
    return html;
  }
  function inr(x, digits) {
    return x == null || isNaN(x) ? "-" : "\u20b9" + Number(x).toLocaleString("en-IN", { minimumFractionDigits: digits == null ? 2 : digits,
                                                                                        maximumFractionDigits: digits == null ? 2 : digits });
  }

  window.dash = { api: api, esc: esc, pct: pct, num: num, when: when, countUp: countUp, nav: nav, navBadge: navBadge,
                  statusPills: statusPills, inr: inr };
})();
