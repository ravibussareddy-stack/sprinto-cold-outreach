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

  window.dash = { api: api, esc: esc };
})();
