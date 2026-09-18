/* Controlled Degradation Bench — leaderboard front-end (no dependencies). Data: window.CDB_DATA. */
(function () {
  "use strict";
  const D = window.CDB_DATA;
  if (!D) { console.error("CDB_DATA missing — run `python -m cdb_score build`"); return; }

  const AXIS = { safety: "Safety", comfort: "Comfort & handling", operation: "Operation" };
  const AXIS_COLOR = { safety: "var(--safety)", comfort: "var(--comfort)", operation: "var(--operation)", cdb_index: "var(--accent)" };
  const SCN = Object.fromEntries(D.matrix.scenarios.map(s => [s.slug, s]));
  const METRIC = Object.fromEntries(D.metrics.map(m => [m.key, m]));
  const fmt = (v, d = 1) => (v == null || Number.isNaN(v)) ? "—" : Number(v).toFixed(d);
  const esc = s => String(s).replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const $ = (sel, root = document) => root.querySelector(sel);
  const real = D.entries.filter(e => e.scores);
  const pending = D.entries.filter(e => !e.scores);

  /* ---------- helpers ---------- */
  function heatColor(r) { // retention 0..1 -> soft red .. soft green
    if (r == null) return "#f1f1f1";
    const h = 8 + 112 * Math.max(0, Math.min(1, r)); // 8 red -> 120 green
    return `hsl(${h} 70% 90%)`;
  }
  function heatText(r) { const h = 8 + 112 * Math.max(0, Math.min(1, r ?? 0)); return `hsl(${h} 60% 28%)`; }
  function ciText(e, k) { const c = e.ci95 && e.ci95[k]; return c ? `${fmt(c[0], 0)}–${fmt(c[1], 0)}` : ""; }

  /* ---------- highlight bar charts ---------- */
  function barChart(el, axisKey) {
    const rows = [...real].sort((a, b) => b.scores[axisKey] - a.scores[axisKey]);
    const slots = [...rows, ...pending.slice(0, Math.max(0, 4 - rows.length))];
    const W = 420, H = 190, padL = 8, padB = 44, padT = 18;
    const bw = Math.min(64, (W - padL * 2) / slots.length - 16);
    const x = i => padL + i * ((W - padL * 2) / slots.length) + ((W - padL * 2) / slots.length - bw) / 2;
    const y = v => padT + (H - padB - padT) * (1 - v / 100);
    let svg = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="${AXIS[axisKey] || "CDB Index"} by model">`;
    [25, 50, 75, 100].forEach(g => svg += `<line x1="${padL}" x2="${W - padL}" y1="${y(g)}" y2="${y(g)}" stroke="var(--line)" stroke-dasharray="2 3"/>`);
    slots.forEach((e, i) => {
      const isReal = !!e.scores;
      const v = isReal ? e.scores[axisKey] : 0;
      const label = e.model.replace("Autoware 0.3.8 · ", "");
      if (isReal) {
        svg += `<rect x="${x(i)}" y="${y(v)}" width="${bw}" height="${y(0) - y(v)}" rx="3" fill="${e.color || "#0a0a0a"}"/>`;
        svg += `<text x="${x(i) + bw / 2}" y="${y(v) - 5}" text-anchor="middle" font-size="12" font-weight="600" fill="var(--fg)">${fmt(v, 0)}</text>`;
      } else {
        svg += `<rect x="${x(i)}" y="${y(12)}" width="${bw}" height="${y(0) - y(12)}" rx="3" fill="var(--bg-soft)" stroke="var(--line-strong)" stroke-dasharray="3 3"/>`;
        svg += `<text x="${x(i) + bw / 2}" y="${y(12) - 5}" text-anchor="middle" font-size="10" fill="var(--muted)">pending</text>`;
      }
      const words = label.replace(/[()·]/g, " ").split(/\s+/).filter(Boolean);
      const l1 = words.slice(0, 2).join(" "), l2 = words.slice(2, 4).join(" ");
      svg += `<text x="${x(i) + bw / 2}" y="${H - padB + 14}" text-anchor="middle" font-size="10" fill="${isReal ? "var(--fg)" : "var(--muted)"}">${esc(l1)}</text>`;
      if (l2) svg += `<text x="${x(i) + bw / 2}" y="${H - padB + 26}" text-anchor="middle" font-size="10" fill="var(--muted)">${esc(l2)}</text>`;
    });
    svg += `</svg>`;
    el.innerHTML = svg;
  }

  /* ---------- leaderboard table ---------- */
  const state = { sort: "cdb_index", dir: "desc", q: "", detector: "all", fusion: "all", controller: "all", status: "all", expanded: false };

  function absOverall(e, key) { return e.absolute_S3 && e.absolute_S3.overall ? e.absolute_S3.overall[key] : null; }
  const COLS = [
    { key: "cdb_index", get: e => e.scores?.cdb_index, primary: true },
    { key: "safety", get: e => e.scores?.safety },
    { key: "comfort", get: e => e.scores?.comfort },
    { key: "operation", get: e => e.scores?.operation },
    { key: "abs_collisions", get: e => absOverall(e, "safety.collision_count") },
    { key: "abs_ttc", get: e => absOverall(e, "safety.min_ttc_s") },
    { key: "abs_completion", get: e => absOverall(e, "task_completion.route_completion") },
    { key: "abs_time", get: e => absOverall(e, "task_completion.completion_time_s") },
    { key: "runs", get: e => e.n_runs },
  ];
  const getter = Object.fromEntries(COLS.map(c => [c.key, c.get]));

  function filteredEntries() {
    const q = state.q.trim().toLowerCase();
    let rows = D.entries.filter(e =>
      (!q || (e.model + " " + e.creator + " " + e.detector).toLowerCase().includes(q)) &&
      (state.detector === "all" || e.detector === state.detector) &&
      (state.fusion === "all" || e.fusion_mode === state.fusion) &&
      (state.controller === "all" || e.controller_profile === state.controller) &&
      (state.status === "all" || e.status === state.status));
    const g = getter[state.sort] || getter.cdb_index;
    rows.sort((a, b) => {
      const va = g(a), vb = g(b);
      if (va == null && vb == null) return 0;
      if (va == null) return 1;
      if (vb == null) return -1;
      return state.dir === "desc" ? vb - va : va - vb;
    });
    return rows;
  }

  function scoreCell(e, k, primary) {
    if (!e.scores) return `<span class="dash">--</span>`;
    const v = e.scores[k];
    const ci = ciText(e, k);
    return `<span class="sbar"><span class="track"><span class="fill" style="width:${v}%;background:${AXIS_COLOR[k]}"></span></span><span class="score${primary ? " primary" : ""} num">${fmt(v, 0)}${ci ? `<span class="ci">${ci}</span>` : ""}</span></span>`;
  }

  function renderTable() {
    const tbody = $("#lb-body");
    if (!tbody) return;
    const rows = filteredEntries();
    tbody.innerHTML = rows.map((e, i) => {
      const isReal = !!e.scores;
      const name = isReal ? `<a href="model.html?id=${encodeURIComponent(e.id)}">${esc(e.model)}</a>` : esc(e.model);
      const meta = `${esc(e.creator)} · ${esc(e.detector)} · ${esc(e.fusion_mode)} · ${esc(e.controller_profile)}`;
      const abs = k => isReal ? fmt(absOverall(e, k), k === "task_completion.route_completion" ? 3 : k === "safety.collision_count" ? 2 : 1) : `<span class="dash">--</span>`;
      const prov = isReal ? `${e.n_runs} <span class="dash">/ 28 cells</span>` : `<a class="btn ghost small" href="submit.html">Submit results</a>`;
      const verified = isReal ? `<span class="pill">✓ hash-verified</span>` : `<span class="dash">--</span>`;
      return `<tr class="${isReal ? "" : "pending"}">
        <td class="model"><span class="ranknum num">${isReal ? e.rank : ""}</span><span class="bar" style="background:${isReal ? e.color : "var(--line-strong)"}"></span>${name}<span class="meta">${meta}</span></td>
        <td class="grp-start">${scoreCell(e, "cdb_index", true)}</td>
        <td>${scoreCell(e, "safety")}</td>
        <td>${scoreCell(e, "comfort")}</td>
        <td>${scoreCell(e, "operation")}</td>
        <td class="grp-start col-abs num">${abs("safety.collision_count")}</td>
        <td class="col-abs num">${abs("safety.min_ttc_s")}</td>
        <td class="col-abs num">${abs("task_completion.route_completion")}</td>
        <td class="col-abs num">${abs("task_completion.completion_time_s")}</td>
        <td class="grp-start col-prov num">${prov}</td>
        <td class="col-prov">${verified}</td>
      </tr>`;
    }).join("");
    document.querySelectorAll("th.sortable").forEach(th => {
      th.classList.remove("sorted-desc", "sorted-asc");
      if (th.dataset.sort === state.sort) th.classList.add(state.dir === "desc" ? "sorted-desc" : "sorted-asc");
    });
    const c = $("#lb-count"); if (c) c.textContent = `${rows.filter(r => r.scores).length} scored · ${rows.filter(r => !r.scores).length} pending of ${D.entries.length} entries`;
  }

  function wireTable() {
    if (!$("#lb-body")) return;
    document.querySelectorAll("th.sortable").forEach(th => th.addEventListener("click", () => {
      const k = th.dataset.sort;
      if (state.sort === k) state.dir = state.dir === "desc" ? "asc" : "desc"; else { state.sort = k; state.dir = "desc"; }
      renderTable();
    }));
    const q = $("#lb-q"); if (q) q.addEventListener("input", () => { state.q = q.value; renderTable(); });
    ["detector", "fusion", "controller", "status"].forEach(k => {
      const sel = $(`#f-${k}`); if (!sel) return;
      const vals = [...new Set(D.entries.map(e => k === "fusion" ? e.fusion_mode : k === "controller" ? e.controller_profile : e[k]))];
      sel.innerHTML = `<option value="all">All</option>` + vals.map(v => `<option value="${esc(v)}">${esc(v)}</option>`).join("");
      sel.addEventListener("change", () => { state[k] = sel.value; renderTable(); });
    });
    const ex = $("#expand-cols"); if (ex) ex.addEventListener("click", () => { state.expanded = !state.expanded; $("#lb-wrap").classList.toggle("expanded", state.expanded); ex.textContent = state.expanded ? "Collapse columns ←|" : "Expand columns |→"; });
    renderTable();
  }

  /* ---------- scenario heat table (index + model page) ---------- */
  function heatTable(el, entry, detailEl) {
    if (!entry) { el.innerHTML = `<p class="dash">No scored entry.</p>`; return; }
    const axes = ["safety", "comfort", "operation"];
    let html = `<div style="overflow-x:auto"><table class="heat"><thead><tr><th></th>${axes.map(a => `<th class="axis"><span class="swatch" style="background:${AXIS_COLOR[a]}"></span> ${AXIS[a]}</th>`).join("")}</tr></thead><tbody>`;
    D.matrix.scenarios.forEach(s => {
      const ps = entry.per_scenario[s.slug];
      html += `<tr><td class="scn">${esc(s.display)}<span class="d">${esc(s.description)}</span></td>`;
      axes.forEach(a => {
        const v = ps && ps.axes[a];
        html += `<td><button class="cell" data-scn="${s.slug}" data-axis="${a}" style="background:${heatColor(v == null ? null : v / 100)};color:${heatText(v == null ? null : v / 100)}">${fmt(v, 0)}</button></td>`;
      });
      html += `</tr>`;
    });
    html += `</tbody></table></div>`;
    el.innerHTML = html;
    el.querySelectorAll(".cell").forEach(b => b.addEventListener("click", () => {
      el.querySelectorAll(".cell").forEach(x => x.classList.remove("sel")); b.classList.add("sel");
      renderCurves(detailEl, entry, b.dataset.scn, b.dataset.axis);
    }));
    const first = el.querySelector(".cell"); if (first && detailEl) { first.classList.add("sel"); renderCurves(detailEl, entry, first.dataset.scn, first.dataset.axis); }
  }

  function sparkline(levels, better) {
    const sev = ["S0", "S1", "S2", "S3"], vals = sev.map(s => levels[s]);
    const ok = vals.filter(v => v != null);
    if (ok.length < 2) return `<svg viewBox="0 0 200 90"></svg>`;
    const lo = Math.min(...ok), hi = Math.max(...ok), span = (hi - lo) || Math.abs(hi) || 1;
    const X = i => 12 + i * (176 / 3), Y = v => 70 - 52 * ((v - lo) / span);
    const pts = vals.map((v, i) => v == null ? null : [X(i), Y(v)]).filter(Boolean);
    const b = vals[0];
    let s = `<svg viewBox="0 0 200 90">`;
    s += `<line x1="12" x2="188" y1="${Y(b)}" y2="${Y(b)}" stroke="var(--line-strong)" stroke-dasharray="3 3"/>`;
    s += `<polyline fill="none" stroke="var(--fg)" stroke-width="1.5" points="${pts.map(p => p.join(",")).join(" ")}"/>`;
    vals.forEach((v, i) => {
      if (v == null) return;
      const worse = better === "lower" ? v > b + 1e-9 : v < b - 1e-9;
      s += `<circle cx="${X(i)}" cy="${Y(v)}" r="3.5" fill="${i === 0 ? "var(--fg)" : worse ? "var(--bad)" : "var(--good)"}"/>`;
      s += `<text x="${X(i)}" y="${Y(v) - 7}" text-anchor="middle" font-size="9" fill="var(--muted)">${fmt(v, Math.abs(v) < 10 ? 2 : 1)}</text>`;
      s += `<text x="${X(i)}" y="86" text-anchor="middle" font-size="9" fill="var(--muted)">${sev[i]}</text>`;
    });
    return s + `</svg>`;
  }

  function renderCurves(el, entry, scn, axis) {
    if (!el) return;
    const ps = entry.per_scenario[scn];
    const ms = D.metrics.filter(m => m.axis === axis);
    const s = SCN[scn];
    el.innerHTML = `<h3>${esc(s.display)} · ${AXIS[axis]} <span class="pill" style="margin-left:6px">${fmt(ps.axes[axis], 0)}</span></h3>
      <p class="sub">Cell means at S0–S3 (n = 5 accepted runs each). Retention r per metric: 1 − clip(worsening ÷ scale). Dashed line = S0 baseline; red points are worse than S0 in the metric's direction, green are not.</p>
      <div class="curves">${ms.map(m => {
        const pm = ps.metrics[m.key] || {};
        const r = pm.retention;
        return `<div class="curve"><div class="t"><span>${esc(m.display)} <span class="dash">(${esc(m.unit)}, ${m.better_when} is better)</span></span><span class="r">r = ${r == null ? "n/a" : fmt(r, 2)}</span></div>${sparkline(pm.levels || {}, m.better_when)}<div class="lv"><span>scale: ${esc(m.scale)}</span>${pm.note ? `<span>${esc(pm.note)}</span>` : ""}</div></div>`;
      }).join("")}</div>`;
  }

  /* ---------- updates list / meta ---------- */
  function renderUpdates() {
    const el = $("#updates"); if (!el) return;
    el.innerHTML = D.updates.map(u => `<div class="update"><div><div class="eyebrow">Update · ${esc(u.date)}</div><div class="title">${esc(u.title)}</div><div class="text">${esc(u.text)}</div></div><div class="arrow">↗</div></div>`).join("");
  }
  function renderMeta() {
    document.querySelectorAll("[data-generated]").forEach(el => el.textContent = (D.generated_at_utc || "").slice(0, 10));
    document.querySelectorAll("[data-spec]").forEach(el => el.textContent = D.spec_version);
  }

  /* ---------- model page ---------- */
  function modelPage() {
    const root = $("#model-page"); if (!root) return;
    const id = new URLSearchParams(location.search).get("id") || (real[0] && real[0].id);
    const e = D.entries.find(x => x.id === id);
    if (!e || !e.scores) { root.innerHTML = `<p>No scored entry <code>${esc(id || "")}</code>. <a href="index.html">Back to the leaderboard</a>.</p>`; return; }
    document.title = `${e.model} — CDB`;
    $("#m-title").textContent = e.model;
    $("#m-sub").innerHTML = `${esc(e.creator)} · detector <code>${esc(e.detector)}</code> · fusion <code>${esc(e.fusion_mode)}</code> · controller <code>${esc(e.controller_profile)}</code> · stack <code>${esc(e.stack_version)}</code>${e.entry && e.entry.commit ? ` · commit <code>${esc(e.entry.commit)}</code>` : ""}`;
    $("#m-notes").textContent = e.notes || "";
    const tiles = $("#m-tiles");
    tiles.innerHTML = ["cdb_index", "safety", "comfort", "operation"].map(k => `<div class="card"><div class="card-title"><h3><span class="swatch" style="background:${AXIS_COLOR[k]}"></span>${k === "cdb_index" ? "CDB Index" : AXIS[k]}</h3>${k === "cdb_index" ? `<span class="pill accent">Rank #${e.rank}</span>` : ""}</div><div class="num" style="font-size:44px;font-family:var(--serif);line-height:1">${fmt(e.scores[k], 1)}</div><div class="desc">95 % bootstrap interval ${ciText(e, k) || "n/a"} · ${k === "cdb_index" ? "equal-weight mean of the three axes" : `${D.metrics.filter(m => m.axis === k).length} metrics × 7 scenarios`}</div></div>`).join("");
    heatTable($("#m-heat"), e, $("#m-detail"));
    // absolute table S0 vs S3
    const absKeys = D.metrics.filter(m => m.absolute_column);
    let t = `<div style="overflow-x:auto"><table class="prose-table lb" style="min-width:600px"><thead><tr class="groups"><th>Scenario</th>${absKeys.map(m => `<th colspan="2" class="grp-start"><span class="g">${esc(m.display)} <span class="u">${esc(m.unit)}</span></span></th>`).join("")}</tr><tr><th></th>${absKeys.map(() => `<th class="grp-start">S0</th><th>S3</th>`).join("")}</tr></thead><tbody>`;
    D.matrix.scenarios.forEach(s => {
      t += `<tr><td style="text-align:left">${esc(s.display)}</td>${absKeys.map(m => { const a = e.absolute_S0[s.slug]?.[m.key], b = e.absolute_S3[s.slug]?.[m.key]; const d = m.key.includes("completion") && !m.key.includes("time") ? 3 : m.key.includes("collision") ? 1 : 2; return `<td class="grp-start num">${fmt(a, d)}</td><td class="num">${fmt(b, d)}</td>`; }).join("")}</tr>`;
    });
    t += `</tbody></table></div>`;
    $("#m-abs").innerHTML = t;
    // provenance
    const p = e.provenance || {};
    $("#m-prov").innerHTML = `<dl class="kv"><dt>Runs</dt><dd>${e.n_runs} accepted (${e.validation.cells_complete} cells, 5 unique repeats each)</dd><dt>Evidence snapshot</dt><dd><code>${esc(p.evidence_snapshot || "")}</code></dd><dt>Hash verification</dt><dd>${esc(p.hashes_verified || "")}</dd><dt>Source manifest sha256</dt><dd><code>${esc((p.source_manifest_sha256 || "").slice(0, 16))}…</code></dd><dt>Validator</dt><dd>${esc(e.validation.status)} · ${e.validation.warnings} warnings (TTC not applicable on the curve; route completion undefined for the controlled stop)</dd><dt>Score spec</dt><dd><code>${esc(D.spec_version)}</code></dd></dl>`;
    // run scatter
    scatter($("#m-scatter"), e);
  }

  function scatter(el, e) {
    if (!el || !e.run_values) return;
    const sel = $("#m-scatter-metric");
    const keys = D.metrics.map(m => m.key);
    sel.innerHTML = keys.map(k => `<option value="${k}">${esc(METRIC[k].display)} (${esc(METRIC[k].unit)})</option>`).join("");
    sel.value = "task_completion.completion_time_s";
    const draw = () => {
      const k = sel.value, m = METRIC[k];
      const W = 900, H = 260, padL = 50, padB = 40, padT = 14;
      const vals = e.run_values.map(r => r.v[k]).filter(v => v != null);
      const lo = Math.min(...vals), hi = Math.max(...vals), span = (hi - lo) || 1;
      const Y = v => padT + (H - padB - padT) * (1 - (v - lo) / span);
      const groupW = (W - padL - 10) / 7;
      let s = `<svg viewBox="0 0 ${W} ${H}" style="width:100%;height:auto;display:block;overflow:visible">`;
      [0, .25, .5, .75, 1].forEach(f => { const v = lo + f * span; s += `<line x1="${padL}" x2="${W - 10}" y1="${Y(v)}" y2="${Y(v)}" stroke="var(--line)"/><text x="${padL - 6}" y="${Y(v) + 3}" text-anchor="end" font-size="10" fill="var(--muted)">${fmt(v, Math.abs(v) < 10 ? 2 : 0)}</text>`; });
      D.matrix.scenarios.forEach((sc, gi) => {
        const gx = padL + gi * groupW;
        s += `<text x="${gx + groupW / 2}" y="${H - 6}" text-anchor="middle" font-size="10.5" fill="var(--fg)">${esc(sc.display)}</text>`;
        ["S0", "S1", "S2", "S3"].forEach((sv, si) => {
          const x = gx + 14 + si * ((groupW - 28) / 3);
          s += `<text x="${x}" y="${H - padB + 14}" text-anchor="middle" font-size="9" fill="var(--muted)">${sv}</text>`;
          const rs = e.run_values.filter(r => r.scenario === sc.slug && r.severity === sv && r.v[k] != null);
          const mean = rs.length ? rs.reduce((a, r) => a + r.v[k], 0) / rs.length : null;
          rs.forEach((r, j) => s += `<circle cx="${x + (j - 2) * 2.2}" cy="${Y(r.v[k])}" r="3" fill="${si === 0 ? "var(--fg)" : "var(--accent)"}" fill-opacity=".55"><title>${esc(r.run_id)}: ${fmt(r.v[k], 3)}</title></circle>`);
          if (mean != null) s += `<line x1="${x - 9}" x2="${x + 9}" y1="${Y(mean)}" y2="${Y(mean)}" stroke="var(--fg)" stroke-width="2"/>`;
        });
      });
      s += `</svg>`;
      el.innerHTML = s;
      $("#m-scatter-cap").textContent = `${m.display} per accepted run (dots) and cell mean (bar), grouped by scenario and severity. ${m.better_when === "lower" ? "Lower" : "Higher"} is better; score scale: ${m.scale}.`;
    };
    sel.addEventListener("change", draw); draw();
  }

  /* ---------- toc highlight ---------- */
  function toc() {
    const links = [...document.querySelectorAll(".toc a[href^='#']")]; if (!links.length) return;
    const secs = links.map(a => document.getElementById(a.getAttribute("href").slice(1))).filter(Boolean);
    const io = new IntersectionObserver(es => es.forEach(en => { if (en.isIntersecting) { links.forEach(l => l.classList.toggle("active", l.getAttribute("href") === "#" + en.target.id)); } }), { rootMargin: "-20% 0px -70% 0px" });
    secs.forEach(s => io.observe(s));
  }

  /* ---------- boot ---------- */
  renderMeta(); renderUpdates();
  ["safety", "comfort", "operation"].forEach(a => { const el = $(`#chart-${a}`); if (el) barChart(el, a); });
  wireTable();
  if ($("#heat")) heatTable($("#heat"), real[0], $("#heat-detail"));
  const hs = $("#heat-entry"); if (hs) { hs.innerHTML = real.map(e => `<option value="${e.id}">${esc(e.model)}</option>`).join(""); hs.addEventListener("change", () => heatTable($("#heat"), D.entries.find(x => x.id === hs.value), $("#heat-detail"))); }
  modelPage(); toc();
})();
