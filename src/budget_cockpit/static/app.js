// Budget Cockpit — small progressive enhancements. No inline scripts (strict CSP).
(function () {
  "use strict";
  const meta = document.querySelector('meta[name="csrf-token"]');
  const csrf = meta ? meta.content : "";
  const grid = document.getElementById("grid");
  const SCROLL_KEY = "budget-cockpit-scroll";

  // Keep the grid position across the reload that follows an edit.
  try {
    const saved = JSON.parse(sessionStorage.getItem(SCROLL_KEY) || "null");
    if (saved && saved.path === location.pathname) {
      if (grid) grid.scrollLeft = saved.x;
      window.scrollTo(0, saved.y);
    }
    sessionStorage.removeItem(SCROLL_KEY);
  } catch (e) { /* storage unavailable: ignore */ }

  function rememberScroll() {
    try {
      sessionStorage.setItem(SCROLL_KEY, JSON.stringify({
        path: location.pathname, x: grid ? grid.scrollLeft : 0, y: window.scrollY,
      }));
    } catch (e) { /* ignore */ }
  }

  async function post(url, body) {
    const res = await fetch(url, {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json", "X-CSRF-Token": csrf },
      body: JSON.stringify(body),
    });
    let data = {};
    try { data = await res.json(); } catch (e) { /* empty body */ }
    if (!res.ok) throw new Error(data.error || "HTTP " + res.status);
    return data;
  }

  function startEdit(td) {
    const original = td.innerHTML;
    const start = td.dataset.value || "";
    const input = document.createElement("input");
    input.type = "text";
    input.inputMode = "decimal";
    input.className = "cell-input";
    input.value = start;
    td.innerHTML = "";
    td.appendChild(input);
    input.focus();
    input.select();
    let done = false;

    function cancel() {
      if (done) return;
      done = true;
      td.innerHTML = original;
    }
    async function commit() {
      if (done) return;
      const value = input.value.trim();
      if (value === start && td.dataset.state === "rule") { cancel(); return; }
      done = true;
      try {
        await post("/budget/cell", {
          line_id: Number(td.dataset.line), month: td.dataset.month, value: value === "" ? null : value,
        });
        rememberScroll();
        location.reload();
      } catch (err) {
        window.alert(err.message);
        td.innerHTML = original;
      }
    }
    input.addEventListener("keydown", function (e) {
      if (e.key === "Enter") { e.preventDefault(); commit(); }
      else if (e.key === "Escape") { e.preventDefault(); cancel(); }
    });
    input.addEventListener("blur", function () {
      if (input.value.trim() === start) cancel(); else commit();
    });
  }

  document.addEventListener("click", async function (ev) {
    const toggle = ev.target.closest(".paid-toggle");
    if (toggle) {
      ev.preventDefault();
      try {
        await post("/budget/paid", { line_id: Number(toggle.dataset.line), month: toggle.dataset.month });
        rememberScroll();
        location.reload();
      } catch (err) { window.alert(err.message); }
      return;
    }
    const td = ev.target.closest("td.cell");
    if (td && !td.querySelector("input")) startEdit(td);
  });

  // Confirmation for destructive forms: <form data-confirm="..."> or <button data-confirm="...">
  document.addEventListener("submit", function (ev) {
    const form = ev.target;
    const button = ev.submitter;
    const message = (button && button.dataset && button.dataset.confirm) || (form.dataset && form.dataset.confirm);
    if (message && !window.confirm(message)) ev.preventDefault();
  });

  // Line form: only show the fields used by the chosen frequency.
  const freq = document.getElementById("frequency");
  if (freq) {
    const update = function () {
      document.querySelectorAll("[data-freq]").forEach(function (el) {
        el.hidden = el.dataset.freq.split(" ").indexOf(freq.value) === -1;
      });
    };
    freq.addEventListener("change", update);
    update();
  }
})();
