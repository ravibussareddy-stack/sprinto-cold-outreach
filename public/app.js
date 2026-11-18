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

  // Navigation shared by every page. Dashboard sections are hash pages inside index.html.
  var NAV = [["overview", "Overview", "index.html#overview"], ["pipeline", "Pipeline", "index.html#pipeline"],
             ["contacts", "Contacts", "index.html#contacts"], ["review", "Review", "index.html#review"],
             ["emails", "Emails", "index.html#emails"], ["learning", "Learning", "index.html#learning"],
             ["costs", "Costs", "index.html#costs"], ["rubric", "Filters & rubric", "settings.html"],
             ["test", "Run a test case", "test.html"]];
  var badges = {};
  function nav(active) {
    var el = document.getElementById("nav");
    if (!el) return;
    el.innerHTML = NAV.map(function (n) {
      var badge = badges[n[0]] ? '<span class="badge">' + badges[n[0]] + "</span>" : "";
      return '<a href="' + n[2] + '" class="' + (n[0] === active ? "on" : "") + '" data-key="' + n[0] + '">' + esc(n[1]) + badge + "</a>";
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

  window.dash = { api: api, esc: esc, pct: pct, num: num, when: when, countUp: countUp, nav: nav, navBadge: navBadge };
})();
