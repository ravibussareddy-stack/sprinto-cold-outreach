// Shared helpers for the dashboard pages. The token is kept in sessionStorage
// (this browser tab only) and sent as a header; it is never put in a URL.
(function () {
  function getToken() {
    let t = null;
    try { t = sessionStorage.getItem("dashToken"); } catch (e) {}
    if (!t) {
      t = window.prompt("Dashboard token:") || "";
      try { sessionStorage.setItem("dashToken", t); } catch (e) {}
    }
    return t;
  }

  async function api(path) {
    const res = await fetch(path, { headers: { "x-dashboard-token": getToken() } });
    if (res.status === 401) {
      try { sessionStorage.removeItem("dashToken"); } catch (e) {}
      throw new Error("Wrong or missing dashboard token. Reload and try again.");
    }
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

  window.dash = { api: api, esc: esc, pct: pct, num: num, when: when, countUp: countUp };
})();
