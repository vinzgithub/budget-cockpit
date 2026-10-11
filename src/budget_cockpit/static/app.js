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

  // Charts: one tooltip per chart, on hover and keyboard focus. Text only (textContent).
  document.querySelectorAll(".chart").forEach(function (fig) {
    const svg = fig.querySelector("svg");
    if (!svg) return;
    const tip = document.createElement("div");
    tip.className = "chart-tip";
    tip.hidden = true;
    fig.appendChild(tip);
    const cross = svg.querySelector(".crosshair");
    const hasLine = !!svg.querySelector(".line");

    function fill(data) {
      tip.replaceChildren();
      const title = document.createElement("div");
      title.className = "tip-title";
      title.textContent = data.t;
      tip.appendChild(title);
      data.rows.forEach(function (row) {
        const line = document.createElement("div");
        line.className = "tip-row";
        const key = document.createElement("span");
        const kind = row[0].indexOf("m-") === 0 ? "key-dot " : row[0].indexOf("b-") === 0 ? "key-bar " : "key-line ";
        key.className = "key " + (row[0] ? kind + row[0] : "key-blank");
        const value = document.createElement("strong");
        value.textContent = row[2];
        const label = document.createElement("span");
        label.textContent = row[1];
        line.append(key, value, label);
        tip.appendChild(line);
      });
    }
    function place(clientX, clientY) {
      const box = fig.getBoundingClientRect();
      let x = clientX - box.left + 14;
      let y = clientY - box.top - tip.offsetHeight - 14;
      if (x + tip.offsetWidth > box.width) x = clientX - box.left - tip.offsetWidth - 14;
      if (y < 0) y = clientY - box.top + 18;
      tip.style.left = Math.max(0, x) + "px";
      tip.style.top = y + "px";
    }
    function show(hit, clientX, clientY) {
      let data;
      try { data = JSON.parse(hit.dataset.tip); } catch (e) { return; }
      fill(data);
      tip.hidden = false;
      svg.querySelectorAll(".bar.on").forEach(function (b) { b.classList.remove("on"); });
      svg.querySelectorAll('.bar[data-i="' + hit.dataset.i + '"]').forEach(function (b) { b.classList.add("on"); });
      if (cross && hasLine) {
        cross.setAttribute("x1", hit.dataset.x);
        cross.setAttribute("x2", hit.dataset.x);
        cross.classList.add("on");
      }
      place(clientX, clientY);
    }
    function hide() {
      tip.hidden = true;
      if (cross) cross.classList.remove("on");
      svg.querySelectorAll(".bar.on").forEach(function (b) { b.classList.remove("on"); });
    }
    svg.querySelectorAll(".hit").forEach(function (hit) {
      hit.addEventListener("pointerenter", function (ev) { show(hit, ev.clientX, ev.clientY); });
      hit.addEventListener("pointermove", function (ev) { place(ev.clientX, ev.clientY); });
      hit.addEventListener("pointerleave", hide);
      hit.addEventListener("focus", function () {
        const r = hit.getBoundingClientRect();
        show(hit, r.left + r.width / 2, r.top + r.height / 3);
      });
      hit.addEventListener("blur", hide);
    });
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
