// Shared helpers for the dashboard pages (no login: anyone with the link can view).
(function () {
  async function api(path, options) {
    let res;
    try { res = await fetch(path, options); }
    catch (e) { throw new Error("Could not reach the server - check your connection and try again"); }
    if (!res.ok) {
      let msg = "";
      try { const j = await res.json(); msg = (j.error && (j.error.message || j.error)) || ""; } catch (e) {}
      throw new Error((msg ? String(msg) : "Request failed") + " (" + res.status + ")");
    }
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
    ["", [["overview", "Overview", "index.html#overview"], ["questions", "Questions", "questions.html"]]],
    ["Outreach", [["pipeline", "Pipeline", "index.html#pipeline"], ["contacts", "Contacts", "index.html#contacts"],
                  ["emails", "Emails", "index.html#emails"]]],
    ["Quality", [["review", "Review", "index.html#review"], ["learning", "Learning", "index.html#learning"]]],
    ["Setup", [["costs", "Costs", "index.html#costs"], ["rubric", "Rubric", "settings.html"],
               ["offerings", "Offerings", "offerings.html"], ["test", "Test case", "test.html"], ["how", "How it works", "how.html"]]],
  ];
  var badges = {};
  var askedCount = false;
  function nav(active) {
    var el = document.getElementById("nav");
    if (!el) return;
    if (!askedCount) {                                 // open questions badge on every page
      askedCount = true;
      api("/api/questions").then(function (q) { navBadge("questions", q.open_count); }).catch(function () {});
    }
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
    ERROR: ["Error", "warn"], REACTIVATED: ["Reactivated", "strong"], awaiting_reply: ["Awaiting reply", ""], no_reply: ["No reply", "soft"],
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

  // ---------- portfolio of frameworks: overview card and Offerings page ----------
  function pfPill(t, c) { return '<span class="pill ' + (c || "") + '">' + esc(t) + "</span>"; }
  function pfCompanies(list) {
    return list.map(function (c) { return '<a href="company.html?domain=' + encodeURIComponent(c.domain) + '">' + esc(c.company) + "</a>" +
      (c.reason ? ' <span class="muted">(' + esc(c.reason) + ")</span>" : ""); }).join(", ");
  }
  function short(n) { return String(n).split(" (")[0]; }
  // el: container; p: insights.portfolio; reload: called after a change; compact: overview (link to Offerings for "add your own")
  function portfolio(el, p, reload, compact) {
    if (!el) return;
    if (!p) { el.innerHTML = '<p class="muted small">Not available.</p>'; return; }
    var html = '<div class="small" style="margin-bottom:12px"><b>Your portfolio:</b> ' +
      p.core.map(function (c) { return pfPill(c, "soft"); }).join(" ") + " " +
      p.accepted.map(function (a) { return '<span class="pill strong">' + esc(short(a.name)) + (a.custom ? " (yours)" : "") +
        ' <button class="x" data-pf="' + (a.custom ? "delete" : "remove") + '" data-id="' + esc(a.id) + '" title="Remove from the portfolio" aria-label="Remove ' +
        esc(a.name) + '">&times;</button></span>'; }).join(" ") +
      (compact ? ' <a class="btn mini" href="offerings.html#add">+ Add your own</a>' : "") + "</div>";
    p.accepted.forEach(function (a) {
      if (a.reopened.length) html += '<div class="note small" style="margin-bottom:10px"><b>' + esc(short(a.name)) + "</b> re-opened: " + pfCompanies(a.reopened) +
        ". They are <b>Reactivated</b> in the Pipeline. Run one again to research it and draft the email: " +
        a.reopened.map(function (c) { return '<a href="test.html?domain=' + encodeURIComponent(c.domain) + '">' + esc(c.company) + "</a>"; }).join(", ") + ".</div>";
    });
    if (!p.candidates.length) html += '<p class="muted small">No suggested certifications waiting for a decision.</p>';
    html += p.candidates.map(function (c) {
      var when = new Date(c.introduced).toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" });
      return '<div class="fw"><div class="row-head" style="margin-bottom:6px"><div><b>' + esc(c.name) + "</b> " + pfPill(c.kind, "soft") +
        ' <span class="muted small">introduced ' + esc(when) + "</span></div>" +
        '<div style="display:flex;gap:8px"><button class="btn" data-pf="dismiss" data-id="' + esc(c.id) + '">Not now</button>' +
        '<button class="btn primary" data-pf="add" data-id="' + esc(c.id) + '">Add to portfolio</button></div></div>' +
        '<div class="small">' + esc(c.introduced_note) + "." + (c.source ? ' <a href="' + esc(c.source) + '" target="_blank" rel="noopener">Official source</a>' : "") + "</div>" +
        '<div class="small muted">Who needs it: ' + esc(c.who) + "</div>" +
        (c.seen_on.length ? '<div class="small">Already seen on: ' + esc(c.seen_on.join(", ")) + "</div>" : "") +
        '<div class="small">' + (c.fits_dormant.length ? "<b>Would re-open " + c.fits_dormant.length + " dormant compan" + (c.fits_dormant.length > 1 ? "ies" : "y") + ":</b> " + pfCompanies(c.fits_dormant)
          : '<span class="muted">No dormant company fits it yet; new companies that fit it would qualify.</span>') + "</div></div>";
    }).join("");
    if (p.dismissed.length) html += '<div class="small muted" style="margin-top:10px">Not now: ' + p.dismissed.map(function (d) {
      return esc(short(d.name)) + ' <button class="btn mini" data-pf="restore" data-id="' + esc(d.id) + '">Show again</button>'; }).join(" ") + "</div>";
    html += '<div class="note small pf-note" style="margin-top:10px"></div>';
    el.innerHTML = html;
    if (el.dataset.wired) return;
    el.dataset.wired = "1";
    el.addEventListener("click", function (ev) {
      var b = ev.target.closest("button[data-pf]");
      if (!b) return;
      if (b.dataset.pf === "delete" && !window.confirm("Delete this framework you added? Companies it re-opened go back to dormant.")) return;
      b.disabled = true;
      portfolioAction({ action: b.dataset.pf, id: b.dataset.id }).then(function (msg) {
        reload && reload();
        setTimeout(function () { var n = el.querySelector(".pf-note"); if (n) { n.textContent = msg; n.className = "note small pf-note ok"; } }, 900);
      }).catch(function (e) { b.disabled = false; var n = el.querySelector(".pf-note"); if (n) { n.textContent = e.message; n.className = "note small pf-note bad"; } });
    });
  }
  function portfolioAction(body) {
    return api("/api/settings", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ portfolio: body }) })
      .then(function (r) {
        var who = r.changed.map(function (c) { return c.company; }).join(", ");
        return r.action === "add" ? r.name + " added to your portfolio. " + (r.changed.length ? r.changed.length + " dormant compan" +
            (r.changed.length > 1 ? "ies are" : "y is") + " active again: " + who + "." : "No dormant company fits it yet; new companies that fit it will qualify.")
          : r.action === "remove" ? r.name + " removed." + (r.changed.length ? " " + r.changed.length + " compan" + (r.changed.length > 1 ? "ies" : "y") + " back to dormant." : "")
          : r.action === "dismiss" ? r.name + " hidden. Show it again any time." : r.name + " is back on the list.";
      });
  }

  // ---------- criteria heads-up, built from the live rubric ----------
  var DORMANT_TEXT = {
    soc2_or_iso: "already holds SOC 2 or ISO 27001 itself (HIPAA or PCI DSS alone stays a prospect: Sprinto adds SOC 2 / ISO 27001)",
    any_named_cert: "already holds any security-compliance certificate itself (SOC 2, ISO 27001, HIPAA, PCI DSS...; GDPR does not count)",
    soc2_and_iso: "already holds both SOC 2 and ISO 27001",
    off: null };
  function criteria(el) {
    if (!el) return;
    el.innerHTML = '<p class="muted small">Loading the live rubric…</p>';
    api("/api/settings").then(function (s) {
      var r = s.rubric, dorm = DORMANT_TEXT[r.certifications.dormant_rule];
      var steps = [
        ["Is it a B2B SaaS company?", "If not: <b>Disqualified</b>. If the website does not say: <b>Manual review</b>."],
        ["Is it already certified?", dorm ? "If it " + esc(dorm) + ": <b>Dormant</b>, no email. The security or trust page is read first, and only the company's own claim counts." : "The dormant rule is off: certified companies are scored like any other."],
        ["Is it too big?", "Public, or more than " + num(r.size.max_employees) + " employees: <b>Disqualified</b>."],
        ["How strong is the fit?", "A 0-100 score from stage, compliance pressure and the certification gap. <b>" + r.decision.qualify_score +
          "+</b> qualified, <b>" + r.decision.review_score + "-" + (r.decision.qualify_score - 1) + "</b> qualified with a flag, below <b>" +
          r.decision.review_score + "</b> stopped. Greenfield points need a security page that shows no certificate."],
        ["Who gets the email?", "The contact you supply, or a decision-maker found with Hunter. Nobody suitable: <b>No contact</b>, nothing written."],
        ["Is the email safe to send?", "11 draft checks plus a disagreement score; above <b>" + r.review.disagreement_threshold +
          "</b> it goes to Review. Every email goes to our own inbox, never the prospect."]];
      el.innerHTML = '<ol class="criteria">' + steps.map(function (x) {
        return "<li><b>" + esc(x[0]) + "</b><span>" + x[1] + "</span></li>"; }).join("") + "</ol>" +
        '<p class="note small"><b>Heads-up:</b> most well-known SaaS companies already hold SOC 2, so they come back <b>Dormant</b>. ' +
        'That is the rule working: they have already bought what Sprinto sells. Earlier-stage companies are the ones that get an email. ' +
        '<a href="settings.html">Change the rules</a></p>';
    }).catch(function () { el.innerHTML = '<p class="muted small">Could not load the rubric.</p>'; });
  }

  // ---------- guided tour: spotlight one element at a time ----------
  function tour(steps, key) {
    steps = steps.filter(function (st) { return !st.el || document.querySelector(st.el); });
    if (!steps.length) return;
    var i = 0, shade = document.createElement("div"), card = document.createElement("div");
    shade.className = "tour-shade"; card.className = "tour-card glass"; card.setAttribute("role", "dialog");
    card.setAttribute("aria-live", "polite");
    document.body.appendChild(shade); document.body.appendChild(card);
    function place() {
      var st = steps[i], target = st.el && document.querySelector(st.el);
      document.querySelectorAll(".tour-on, .tour-lift").forEach(function (x) { x.classList.remove("tour-on", "tour-lift"); });
      card.innerHTML = '<div class="tour-step">' + (i + 1) + " of " + steps.length + "</div><h3>" + esc(st.title) + "</h3><p>" + st.text + "</p>" +
        '<div class="tour-btns"><button class="btn" data-t="skip">' + (i === steps.length - 1 ? "Close" : "Skip tour") + "</button><span></span>" +
        (i ? '<button class="btn" data-t="back">Back</button>' : "") +
        '<button class="btn primary" data-t="next">' + (i === steps.length - 1 ? "Done" : "Next") + "</button></div>";
      if (target) {
        target.classList.add("tour-on");
        var host = target.parentElement && target.parentElement.closest(".nav, .top");   // own stacking context: lift it too
        if (host) host.classList.add("tour-lift");
        var r0 = target.getBoundingClientRect();
        var pinned = target.closest(".nav");            // sticky: always on screen, scrolling would only move the page
        if (!pinned && (r0.top < 70 || r0.bottom > window.innerHeight - 40)) target.scrollIntoView({ block: r0.height > window.innerHeight * 0.6 ? "start" : "center" });
        position();
      } else {
        card.style.width = Math.min(420, window.innerWidth - 32) + "px";
        card.style.left = (window.innerWidth - Math.min(420, window.innerWidth - 32)) / 2 + "px";
        card.style.top = "22vh";
      }
      var nextBtn = card.querySelector('[data-t="next"]'); if (nextBtn) nextBtn.focus({ preventScroll: true });
    }
    function position() {
      var st = steps[i], target = st && st.el && document.querySelector(st.el);
      if (!target) return;
      var r = target.getBoundingClientRect(), cw = Math.min(360, window.innerWidth - 32);
      card.style.width = cw + "px";
      var h = card.offsetHeight, below = r.bottom + 14 + h < window.innerHeight, above = r.top - h - 14 >= 16;
      if (!below && !above) {                          // tall element: sit in the bottom-right corner over it
        card.style.left = (window.innerWidth - cw - 16) + "px";
        card.style.top = (window.innerHeight - h - 16) + "px";
        return;
      }
      card.style.left = Math.max(16, Math.min(window.innerWidth - cw - 16, r.left)) + "px";
      card.style.top = (below ? r.bottom + 14 : r.top - h - 14) + "px";
    }
    function end() {
      window.removeEventListener("scroll", position, true); window.removeEventListener("resize", position);
      document.querySelectorAll(".tour-on, .tour-lift").forEach(function (x) { x.classList.remove("tour-on", "tour-lift"); });
      shade.remove(); card.remove(); document.removeEventListener("keydown", keys);
      try { localStorage.setItem(key || "tour-done", "1"); } catch (e) {}
    }
    function keys(ev) {
      if (ev.key === "Escape") end();
      else if (ev.key === "ArrowRight") go(1);
      else if (ev.key === "ArrowLeft" && i) go(-1);
    }
    function go(d) { i += d; if (i >= steps.length) return end(); place(); }
    card.addEventListener("click", function (ev) {
      var b = ev.target.closest("button"); if (!b) return;
      if (b.dataset.t === "skip") end(); else go(b.dataset.t === "back" ? -1 : 1);
    });
    shade.addEventListener("click", end);
    document.addEventListener("keydown", keys);
    window.addEventListener("scroll", position, true); window.addEventListener("resize", position);
    place();
  }
  function tourSeen(key) { try { return localStorage.getItem(key || "tour-done") === "1"; } catch (e) { return true; } }

  window.dash = { api: api, esc: esc, pct: pct, num: num, when: when, countUp: countUp, nav: nav, navBadge: navBadge,
                  statusPills: statusPills, inr: inr, criteria: criteria, tour: tour, tourSeen: tourSeen,
                  portfolio: portfolio, portfolioAction: portfolioAction };
})();
