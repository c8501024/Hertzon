// Hertzon Bench comparison site: ranking, single results, systems, versions. Plain JavaScript, no build.
"use strict";

const REPO = "c8501024/Hertzon";
const app = document.getElementById("app");
let SPEC = null;          // suite.json: tests, parts, titles, reference per suite
let RESULTS = null;       // /api/results
const state = { suite: "", version: "", platform: "", arch: "", solver: "", device: "", q: "",
  mode: "multi", metric: "overall", group: true, sort: "score", filters: false };

const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const sec = (x) => (x === null || x === undefined ? "" : `${Number(x).toFixed(1)} s`);
const fmt = (x, d = 0) => (x === null || x === undefined || Number.isNaN(x) ? "" : Number(x).toFixed(d));
const date = (t) => new Date(t * 1000).toISOString().slice(0, 10);
const median = (a) => { const s = [...a].sort((x, y) => x - y); const m = s.length >> 1; return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2; };
const vcmp = (a, b) => { const pa = String(a).split(/[.\-]/).map(Number), pb = String(b).split(/[.\-]/).map(Number);
  for (let i = 0; i < Math.max(pa.length, pb.length); i++) { const d = (pa[i] || 0) - (pb[i] || 0); if (d) return d; } return 0; };
const spec = () => SPEC[state.suite];
const parts = (s = spec()) => (s && s.parts) || [];
const partTitle = (k) => (parts().find((p) => p.id === k) || {}).title || k;

async function load() {
  if (!SPEC) SPEC = await (await fetch("suite.json")).json();
  if (!state.suite) state.suite = Object.keys(SPEC).sort().pop();
  if (!RESULTS) {
    try { RESULTS = (await (await fetch("api/results")).json()).results || []; }
    catch { RESULTS = []; }
  }
}

// ---------------------------------------------------------------- scores
// multi-core: overall, a part (rf, rcs) or one test; single-core: overall or a part
function scoreOf(r, mode = state.mode, metric = state.metric) {
  if (mode === "single") return metric === "overall" ? r.single_core : ((r.single || {})[metric] || {}).score;
  if (metric === "overall") return r.score;
  if (parts().some((p) => p.id === metric)) return ((r.parts || {})[metric] || {}).score;
  const t = (r.tests || []).find((x) => x.id === metric);
  return t ? t.score : null;
}

function metricTitle() {
  const m = state.metric === "overall" ? "" : parts().some((p) => p.id === state.metric) ? partTitle(state.metric) + " "
    : (spec().titles[state.metric] || state.metric) + ": ";
  return `${m}${state.mode === "single" ? "single-core" : "multi-core"} score`;
}

function filtered() {
  const q = state.q.toLowerCase();
  return RESULTS.filter((r) => r.suite === state.suite
    && (!state.version || r.hertzon === state.version)
    && (!state.platform || r.platform === state.platform)
    && (!state.arch || r.arch === state.arch)
    && (!state.solver || r.solver === state.solver)
    && (!state.device || r.device_type === state.device)
    && (!q || `${r.cpu} ${r.gpu} ${r.device} ${r.name} ${r.os}`.toLowerCase().includes(q))
    && scoreOf(r));
}

// one row per system (same hardware, solver and version): median score, best and number of runs
function grouped(rows) {
  const g = new Map();
  for (const r of rows) {
    if (!g.has(r.system_key)) g.set(r.system_key, []);
    g.get(r.system_key).push(r);
  }
  return [...g.values()].map((runs) => {
    const s = runs.map((r) => scoreOf(r));
    return { ...runs[0], score_med: median(s), score_best: Math.max(...s), runs: runs.length, ids: runs.map((r) => r.id) };
  });
}

// what a run lacks for the overall score: parts in which a test did not count
function missingParts(r) {
  return parts(SPEC[r.suite]).map((p) => {
    const x = (r.parts || {})[p.id];
    return !x ? "" : !x.score ? `${p.title}: ${x.counted} of ${x.tests} tests count` : "";
  }).filter(Boolean);
}

// the speedup at the most cores of the scaling benchmark (the chosen part, else RF, else RCS)
function speedupOf(r) {
  const sg = r.single || {};
  const k = sg[state.metric] ? state.metric : ["rf", "rcs"].find((x) => sg[x]);
  const s = k && sg[k];
  return s && s.speedup ? `${fmt(s.speedup, 1)}× <span class="sub">at ${s.cores}</span>` : "";
}

// ---------------------------------------------------------------- pieces
function options(values, cur, all) {
  return `<option value="">${all}</option>` + [...new Set(values.filter(Boolean))].sort()
    .map((v) => `<option ${v === cur ? "selected" : ""}>${esc(v)}</option>`).join("");
}

function systemCell(r) {
  const sub = [r.device, r.gpu].filter(Boolean).join(" · ");
  const miss = state.mode === "multi" && !r.complete ? missingParts(r) : [];
  return `<td class="wrap sys"><b>${esc(r.cpu)}</b>${r.name ? `<span class="tag">${esc(r.name)}</span>` : ""}
    ${miss.map((m) => `<span class="tag warn" title="A part has a score only when every one of its tests counts">${esc(m)}</span>`).join("")}
    ${sub ? `<div class="sub">${esc(sub)}</div>` : ""}</td>`;
}

const rankBadge = (i) => `<span class="rank${i < 3 ? ` r${i + 1}` : ""}">${i + 1}</span>`;

function rankingTable(rows) {
  const g = state.group ? grouped(rows) : rows.map((r) => ({ ...r, score_med: scoreOf(r), score_best: scoreOf(r), runs: 1, ids: [r.id] }));
  const key = { score: (r) => -r.score_med, cores: (r) => -r.cores, memory: (r) => -(r.memory_gb || 0),
    bandwidth: (r) => -(r.bandwidth || 0), version: () => 0, date: (r) => -r.created }[state.sort] || ((r) => -r.score_med);
  g.sort((a, b) => key(a) - key(b) || (state.sort === "version" ? vcmp(b.hertzon, a.hertzon) : 0) || b.score_med - a.score_med);
  if (!g.length) return `<div class="empty"><b>No results here yet</b><br>Run Hertzon Bench in Hertzon (Tools &gt; Solver Benchmark) and press Submit Results.</div>`;
  const top = Math.max(...g.map((r) => r.score_med));
  const th = (k, label, cls = "") => `<th class="sort ${cls}${state.sort === k ? " on" : ""}" data-sort="${k}">${label}${state.sort === k ? " ↓" : ""}</th>`;
  const single = state.mode === "single";
  return `<div class="tbl-wrap"><table class="rank-tbl"><thead><tr><th class="num">#</th><th>System</th><th class="opt">OS</th>
    ${th("memory", "Memory", "num opt")}${th("bandwidth", "Bandwidth", "num opt")}${th("cores", "Cores", "num")}<th class="opt">Solver</th>
    ${th("version", "Hertzon", "opt")}${single ? `<th class="num opt">Speedup</th>` : ""}${th("score", "Score")}
    ${th("date", state.group ? "Runs" : "Date", "num opt")}</tr></thead><tbody>
    ${g.map((r, i) => `<tr><td class="num">${rankBadge(i)}</td>${systemCell(r)}
      <td class="opt">${esc(r.platform)}<div class="sub">${esc(r.os || "")}</div></td>
      <td class="num opt">${r.memory_gb ? fmt(r.memory_gb) + " GB" : ""}</td>
      <td class="num opt">${r.bandwidth ? fmt(r.bandwidth, 1) + " GB/s" : ""}</td>
      <td class="num">${r.cores}</td><td class="opt">${esc(r.solver)}</td><td class="opt">${esc(r.hertzon)}</td>
      ${single ? `<td class="num opt">${speedupOf(r)}</td>` : ""}
      <td class="bar"><i style="width:${(100 * r.score_med / top).toFixed(1)}%"></i>
        <b><a href="${r.runs > 1 ? `#/system/${r.system_key}` : `#/result/${r.ids[0]}`}">${fmt(r.score_med)}</a></b>
        ${r.runs > 1 ? `<span class="sub">best ${fmt(r.score_best)}</span>` : ""}</td>
      <td class="num opt">${state.group ? `<a href="#/system/${r.system_key}">${r.runs}</a>` : date(r.created)}</td></tr>`).join("")}
    </tbody></table></div>`;
}

function modeBar(withTests = true) {
  const s = spec();
  const chip = (v, label) => `<button data-metric="${v}" class="${state.metric === v ? "on" : ""}">${esc(label)}</button>`;
  const tests = withTests && state.mode === "multi" ? `<label class="inline">Single test<select data-f="metric-test"><option value="">–</option>
    ${s.tests.map((t) => `<option value="${t.id}" ${t.id === state.metric ? "selected" : ""}>${esc(s.titles[t.id])}</option>`).join("")}</select></label>` : "";
  return `<div class="modes">
    <div class="mode-tabs" role="tablist">
      <button data-mode="multi" class="${state.mode === "multi" ? "on" : ""}"><b>Multi-Core</b><span>full benchmark, all cores</span></button>
      <button data-mode="single" class="${state.mode === "single" ? "on" : ""}"><b>Single-Core</b><span>scaling benchmark, 1 core</span></button>
    </div>
    <div class="chips">${chip("overall", "Overall")}${parts().map((p) => chip(p.id, p.title)).join("")}${tests}</div>
  </div>`;
}

function filterBar(all) {
  const active = ["version", "platform", "arch", "device", "solver"].filter((k) => state[k]).length;
  return `<div class="toolbar">
    <label class="search"><svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true"><circle cx="11" cy="11" r="7" fill="none" stroke="currentColor" stroke-width="2"/><path d="M20 20l-4-4" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>
      <input data-f="q" value="${esc(state.q)}" placeholder="Search CPU, GPU, device, name" aria-label="Search"></label>
    <button class="ghost-btn${state.filters ? " on" : ""}" data-toggle="filters">Filters${active ? ` <span class="count">${active}</span>` : ""}</button>
    <span class="seg"><button data-group="1" class="${state.group ? "on" : ""}">Per system</button><button data-group="0" class="${state.group ? "" : "on"}">All runs</button></span>
  </div>
  <div class="filters${state.filters ? "" : " hidden"}">
    <label>Suite<select data-f="suite">${Object.keys(SPEC).sort().map((s) => `<option ${s === state.suite ? "selected" : ""}>${esc(s)}</option>`).join("")}</select></label>
    <label>Hertzon<select data-f="version">${options(all.map((r) => r.hertzon).sort(vcmp).reverse(), state.version, "All versions")}</select></label>
    <label>OS<select data-f="platform">${options(all.map((r) => r.platform), state.platform, "All")}</select></label>
    <label>Architecture<select data-f="arch">${options(all.map((r) => r.arch), state.arch, "All")}</select></label>
    <label>Device<select data-f="device">${options(all.map((r) => r.device_type), state.device, "All")}</select></label>
    <label>Solver<select data-f="solver">${options(all.map((r) => r.solver), state.solver, "All")}</select></label>
  </div>`;
}

function bindFilters(render) {
  app.querySelectorAll("[data-f]").forEach((el) => el.addEventListener(el.tagName === "INPUT" ? "input" : "change", () => {
    if (el.dataset.f === "metric-test") state.metric = el.value || "overall";
    else state[el.dataset.f] = el.value;
    if (el.dataset.f === "suite") state.metric = "overall";
    const focus = el.tagName === "INPUT";
    render();
    if (focus) { const i = app.querySelector('[data-f="q"]'); i.focus(); i.setSelectionRange(i.value.length, i.value.length); }
  }));
  app.querySelectorAll("[data-mode]").forEach((b) => b.addEventListener("click", () => {
    state.mode = b.dataset.mode;
    if (state.mode === "single" && !["overall", ...parts().map((p) => p.id)].includes(state.metric)) state.metric = "overall";
    render();
  }));
  app.querySelectorAll("[data-metric]").forEach((b) => b.addEventListener("click", () => { state.metric = b.dataset.metric; render(); }));
  app.querySelectorAll("[data-group]").forEach((b) => b.addEventListener("click", () => { state.group = b.dataset.group === "1"; render(); }));
  app.querySelectorAll("[data-toggle=filters]").forEach((b) => b.addEventListener("click", () => { state.filters = !state.filters; render(); }));
  app.querySelectorAll("th[data-sort]").forEach((th) => th.addEventListener("click", () => { state.sort = th.dataset.sort; render(); }));
}

function hero(title, lead, extra = "") {
  return `<section class="hero"><div class="hero-in"><p class="eyebrow">Hertzon Bench</p><h1>${title}</h1>
    ${lead ? `<p class="hero-lead">${lead}</p>` : ""}${extra}</div></section>`;
}

const stat = (value, label) => `<div class="stat"><b>${value}</b><span>${label}</span></div>`;

// ---------------------------------------------------------------- views
function viewRanking() {
  const all = RESULTS.filter((r) => r.suite === state.suite);
  const s = spec();
  const best = Math.max(0, ...all.map((r) => r.score || 0));
  app.innerHTML = hero(`How fast does your computer <em>solve Maxwell</em>?`,
    `${s.tests.filter((t) => !t.large).length} fixed FEM models in two parts, ${parts().map((p) => `<b>${esc(p.title)}</b>`).join(" and ")},
    solved with Hertzon and checked against a reference. 1000 is the reference computer, 2000 is twice as fast.`,
    `<div class="stats">${stat(all.length, "results")}${stat(new Set(all.map((r) => r.hw_key)).size, "systems")}
      ${stat(new Set(all.map((r) => r.hertzon)).size, "Hertzon versions")}${stat(best ? fmt(best) : "–", "best multi-core score")}</div>`)
    + `<div class="page">${modeBar()}${filterBar(all)}
    <p class="note">${state.mode === "single"
      ? "Single-core scores come from the scaling benchmark: one test per part solved with 1, 2, 4, ... cores; the speedup is the one at the most cores."
      : "A part has a score only when every one of its tests counted, the overall score only when every part has one."}
      ${state.group ? " One row per system: the median of its runs." : ""} <a href="#/about">How it is measured</a></p>
    <h2 class="tbl-title">${esc(metricTitle()[0].toUpperCase() + metricTitle().slice(1))}</h2>
    ${rankingTable(filtered())}</div>`;
  bindFilters(viewRanking);
}

function card(title, pairs) {
  const rows = pairs.filter(([, v]) => v !== undefined && v !== null && v !== "" && !(Array.isArray(v) && !v.length));
  return `<div class="card"><h3>${title}</h3><dl class="kv">${rows.map(([k, v]) => `<dt>${esc(k)}</dt><dd>${esc(v)}</dd>`).join("")}</dl></div>`;
}

function scoreTile(label, value, sub, main = false) {
  return `<div class="tile${main ? " main" : ""}${value ? "" : " none"}"><span>${esc(label)}</span><b>${value ? fmt(value) : "–"}</b>${sub ? `<small>${sub}</small>` : ""}</div>`;
}

async function viewResult(id) {
  const r = await fetch(`api/result/${encodeURIComponent(id)}`);
  if (!r.ok) { app.innerHTML = `<div class="page"><div class="empty">Result not found.</div></div>`; return; }
  const d = await r.json();
  const sp = SPEC[d.suite] || { titles: {}, tests: [], parts: [] };
  const s = d.system || {}, sw = s.software || {};
  const same = RESULTS.filter((x) => x.suite === d.suite && x.id !== d.id && x.score);
  const me = RESULTS.find((x) => x.id === d.id);
  const better = d.score ? same.filter((x) => x.score > d.score).length : 0;
  const miss = d.complete === false ? missingParts(d) : [];
  const sc = d.scaling || null;
  const multi = parts(sp).some((p) => (d.parts || {})[p.id]);
  app.innerHTML = hero(esc(s.cpu || "Result"), `${esc([s.device, s.os || s.platform, s.memory_gb && `${s.memory_gb} GB`, `${d.cores} cores`, d.solver].filter(Boolean).join(" · "))}
      <br><span class="dim">${esc(d.suite)} · Hertzon ${esc(sw.hertzon || "")} · submitted ${date(d.created)}${d.name ? ` by ${esc(d.name)}` : ""}</span>`)
    + `<div class="page"><p class="crumbs"><a href="#/">Ranking</a> › Result ${esc(d.id)}${me ? ` · <a href="#/system/${me.system_key}">all runs of this system</a>` : ""}</p>
    ${multi ? `<h2>Multi-core</h2><div class="tiles">${scoreTile("Overall", d.score, d.score ? `rank ${better + 1} of ${same.length + 1}` : "needs every part", true)}
      ${parts(sp).map((p) => { const x = (d.parts || {})[p.id]; return scoreTile(p.title, x && x.score, x ? (x.score ? `${x.tests} tests` : `${x.counted} of ${x.tests} tests count`) : "not run"); }).join("")}</div>
      ${miss.length ? `<p class="bad">No overall score: ${esc(miss.join("; "))}. A part has a score only when every one of its tests counts; the reasons are in the table below.</p>` : ""}` : ""}
    ${sc ? `<h2>Single-core and scaling</h2><div class="tiles">${scoreTile("Overall", sc.single_core_score, sc.single_core_score ? "" : "needs every part", true)}
      ${parts(sp).map((p) => { const x = (sc.parts || {})[p.id]; return scoreTile(p.title, x && x.single_core_score, x ? (x.single_core_score ? esc(sp.titles[x.test] || x.test) : esc(x.reason || "")) : "not run"); }).join("")}</div>
      ${scalingChart(sc, sp)}` : ""}
    ${d.comment ? `<p class="comment">“${esc(d.comment)}”</p>` : ""}
    <div class="cards">
      ${card("System", [["Device", s.device], ["Type", s.device_type], ["CPU", s.cpu],
        ["Cores", s.physical_cores ? `${s.physical_cores} physical, ${s.logical_cores} logical` : s.logical_cores],
        ["P / E cores", s.performance_cores ? `${s.performance_cores} / ${s.efficiency_cores || 0}` : ""],
        ["Clock", s.cpu_max_mhz ? `${s.cpu_max_mhz} MHz max` : ""],
        ["Memory", [s.memory_gb && `${s.memory_gb} GB`, s.memory_type, s.memory_speed_mts && `${s.memory_speed_mts} MT/s`,
          s.memory_modules && `${s.memory_modules} modules`].filter(Boolean).join(", ")],
        ["Memory bandwidth", s.memory_bandwidth_gbs ? `${s.memory_bandwidth_gbs} GB/s (measured, one core)` : ""],
        ["GPU", (s.gpus || []).map((g) => g.name + (g.memory_gb ? ` (${g.memory_gb} GB)` : "") + (g.cores ? ` (${g.cores} cores)` : "")).join(", ")],
        ["OS", s.os || s.platform], ["Architecture", s.arch], ["Power", [s.power_source, s.power_plan].filter(Boolean).join(", ")],
        ["Virtual machine", s.virtual_machine ? (s.virtualization || "yes") : "no"]])}
      ${card("Software", [["Hertzon", sw.hertzon], ["EMerge", sw.emerge], ["Python", sw.python], ["NumPy", sw.numpy],
        ["SciPy", sw.scipy], ["BLAS", sw.blas], ["Matrix solver", d.solver], ["Cores used", d.cores]])}
    </div>
    ${(d.tests || []).length ? `<h2>Tests</h2>${testsTable(d, sp)}` : ""}</div>`;
}

function checkText(t) {
  if (t.counted === false) return `Not counted: ${esc(t.reason)}`;
  const c = t.check;
  if (!c) return "";
  if (c.value === null || c.value === undefined) return "ok";
  if (c.kind === "rcs_db") return `${fmt(c.value, 2)} dB vs. Mie`;
  if (c.kind === "rcs_ref_db") return `${fmt(c.value, 2)} dB vs. reference`;
  return `|ΔS| ${Number(c.value).toExponential(1)}`;
}

function testsTable(d, sp) {
  const partOf = Object.fromEntries(parts(sp).flatMap((p) => p.tests.map((id) => [id, p.title])));
  return `<div class="tbl-wrap"><table><thead><tr><th>Test</th><th class="opt">Part</th><th class="num">Total</th><th class="num opt">Initial mesh</th>
    <th class="num opt">Adaptive passes</th><th class="num opt">Sweep</th><th class="num">Passes</th><th class="num opt">Tetrahedra</th>
    <th class="num opt">Peak memory</th><th>Result check</th><th class="num">Score</th></tr></thead><tbody>
    ${d.tests.map((t) => {
      const large = (sp.tests.find((x) => x.id === t.id) || {}).large;
      const name = `${esc(sp.titles[t.id] || t.id)}${large ? '<span class="tag">large, own score</span>' : ""}`;
      if (t.skipped || t.total === undefined)
        return `<tr><td>${name}</td><td class="opt">${esc(partOf[t.id] || "")}</td><td colspan="7"></td>
          <td class="wrap ${t.skipped ? "muted" : "bad"}">${t.skipped ? `Not run: ${esc(t.skipped)}` : `Not counted: ${esc(t.reason)}`}</td><td></td></tr>`;
      return `<tr><td>${name}</td><td class="opt">${esc(partOf[t.id] || "")}</td><td class="num">${sec(t.total)}</td>
        <td class="num opt">${sec(t.mesh)}</td><td class="num opt">${sec(t.adaptive)}</td><td class="num opt">${sec(t.sweep)}</td>
        <td class="num">${t.passes ?? ""}</td><td class="num opt">${t.tets ?? ""}</td><td class="num opt">${t.memory_mb ? fmt(t.memory_mb / 1024, 1) + " GB" : ""}</td>
        <td class="wrap ${t.counted !== false && t.check && t.check.ok ? "ok" : "bad"}">${checkText(t)}</td>
        <td class="num"><b>${fmt(t.score)}</b></td></tr>`;
    }).join("")}</tbody></table></div>`;
}

// speedup over the cores, one line per part, with the ideal (linear) speedup; log2 axis for the cores
function scalingChart(sc, sp) {
  const series = parts(sp).map((p, i) => ({ p, i, x: (sc.parts || {})[p.id] }))
    .filter((s) => s.x && (s.x.runs || []).some((r) => r.speedup));
  if (!series.length) return "";
  const maxC = Math.max(2, ...series.flatMap((s) => s.x.runs.map((r) => r.cores)));
  const maxS = Math.max(2, ...series.flatMap((s) => s.x.runs.map((r) => r.speedup || 0)));
  const yTop = maxC;                                   // log-log: the ideal speedup is the diagonal
  const W = 640, H = 280, L = 46, R = 16, T = 14, B = 42;
  const lx = (c) => L + (W - L - R) * Math.log2(c) / Math.log2(maxC);
  const ly = (v) => H - B - (H - T - B) * Math.log2(v) / Math.log2(yTop);
  const ticks = []; for (let c = 1; c < maxC; c *= 2) ticks.push(c);
  ticks.push(maxC);
  const yt = ticks.filter((v, k) => k === 0 || v <= yTop);
  const line = (pts) => pts.map(([x, y], k) => `${k ? "L" : "M"}${x.toFixed(1)},${y.toFixed(1)}`).join("");
  const svg = `<svg class="chart" viewBox="0 0 ${W} ${H}" role="img" aria-label="Speedup over the number of cores">
    ${yt.map((v) => `<line class="grid" x1="${L}" x2="${W - R}" y1="${ly(v)}" y2="${ly(v)}"/><text class="ax" x="${L - 8}" y="${ly(v) + 4}" text-anchor="end">${v}×</text>`).join("")}
    ${ticks.map((c) => `<text class="ax" x="${lx(c)}" y="${H - B + 18}" text-anchor="middle">${c}</text>`).join("")}
    <text class="ax" x="${(L + W - R) / 2}" y="${H - 6}" text-anchor="middle">cores</text>
    <path class="ideal" d="${line([[lx(1), ly(1)], [lx(yTop), ly(yTop)]])}"/>
    ${series.map((s) => { const runs = [...s.x.runs].filter((r) => r.speedup).sort((a, b) => a.cores - b.cores);
      return `<path class="ln s${s.i}" d="${line(runs.map((r) => [lx(r.cores), ly(r.speedup)]))}"/>
        ${runs.map((r) => `<circle class="s${s.i}" cx="${lx(r.cores)}" cy="${ly(r.speedup)}" r="4.5"><title>${esc(s.p.title)}: ${r.cores} cores, ${fmt(r.total, 1)} s, ${fmt(r.speedup, 2)}×</title></circle>`).join("")}`; }).join("")}
  </svg>`;
  const legend = series.map((s) => `<span><i class="s${s.i}"></i>${esc(s.p.title)} (${esc(sp.titles[s.x.test] || s.x.test)})</span>`).join("") + `<span><i class="ideal"></i>ideal</span>`;
  const table = `<div class="tbl-wrap"><table><thead><tr><th>Part</th><th class="num">Cores</th><th class="num">Total</th><th class="num opt">Adaptive passes</th>
    <th class="num opt">Sweep</th><th class="num">Speedup</th><th class="num">Efficiency</th></tr></thead><tbody>
    ${series.flatMap((s) => [...s.x.runs].sort((a, b) => a.cores - b.cores).map((r) => `<tr><td>${esc(s.p.title)}</td><td class="num">${r.cores}</td>
      <td class="num">${fmt(r.total, 1)} s</td><td class="num opt">${sec(r.adaptive)}</td><td class="num opt">${sec(r.sweep)}</td>
      <td class="num">${r.speedup ? fmt(r.speedup, 2) + "×" : ""}</td><td class="num">${r.efficiency ? fmt(100 * r.efficiency) + " %" : ""}</td></tr>`)).join("")}
    </tbody></table></div>`;
  return `<div class="chart-card"><div class="legend">${legend}</div>${svg}</div>${table}`;
}

function viewSystem(key) {
  const runs = RESULTS.filter((r) => r.system_key === key).sort((a, b) => b.created - a.created);
  if (!runs.length) { app.innerHTML = `<div class="page"><div class="empty">No results for this system.</div></div>`; return; }
  const r0 = runs[0], sp = SPEC[r0.suite];
  const med = (f) => { const s = runs.map(f).filter(Boolean); return s.length ? median(s) : null; };
  const other = RESULTS.filter((r) => r.hw_key === r0.hw_key && r.system_key !== key && r.score);
  const byVer = new Map();
  for (const r of [...runs, ...other].filter((x) => x.score)) { if (!byVer.has(r.hertzon)) byVer.set(r.hertzon, []); byVer.get(r.hertzon).push(r.score); }
  const s = runs.map((r) => r.score).filter(Boolean);
  app.innerHTML = hero(esc(r0.cpu), esc([r0.device, r0.gpu, r0.os, r0.memory_gb && r0.memory_gb + " GB", `${r0.cores} cores`, r0.solver, `Hertzon ${r0.hertzon}`].filter(Boolean).join(" · ")))
    + `<div class="page"><p class="crumbs"><a href="#/">Ranking</a> › System</p>
    <h2>Median of ${runs.length} run${runs.length > 1 ? "s" : ""}</h2>
    <div class="tiles">${scoreTile("Multi-core", med((r) => r.score), s.length > 1 ? `spread ${fmt(100 * (Math.max(...s) - Math.min(...s)) / median(s))} %` : "", true)}
      ${parts(sp).map((p) => scoreTile(`${p.title} multi-core`, med((r) => ((r.parts || {})[p.id] || {}).score), "")).join("")}
      ${scoreTile("Single-core", med((r) => r.single_core), "", true)}
      ${parts(sp).map((p) => scoreTile(`${p.title} single-core`, med((r) => ((r.single || {})[p.id] || {}).score), "")).join("")}</div>
    ${byVer.size > 1 ? `<div class="cards"><div class="card"><h3>Same hardware, other Hertzon versions</h3><dl class="kv">${[...byVer.entries()].sort((a, b) => vcmp(b[0], a[0]))
      .map(([v, x]) => `<dt>${esc(v)}</dt><dd>${fmt(median(x))} <span class="sub">(${x.length} run${x.length > 1 ? "s" : ""})</span></dd>`).join("")}</dl></div></div>` : ""}
    <h2>Runs</h2><div class="tbl-wrap"><table><thead><tr><th>Date</th><th>Name</th>
      ${sp.tests.map((t) => `<th class="num opt">${esc(sp.titles[t.id])}</th>`).join("")}
      ${parts(sp).map((p) => `<th class="num">${esc(p.title)}</th>`).join("")}<th class="num">Multi-core</th><th class="num">Single-core</th></tr></thead><tbody>
    ${runs.map((r) => `<tr><td><a href="#/result/${r.id}">${date(r.created)}</a></td><td>${esc(r.name)}</td>
      ${sp.tests.map((t) => { const x = (r.tests || []).find((y) => y.id === t.id); return `<td class="num opt">${x ? fmt(x.score) : ""}</td>`; }).join("")}
      ${parts(sp).map((p) => `<td class="num">${fmt(((r.parts || {})[p.id] || {}).score)}</td>`).join("")}
      <td class="num"><b>${fmt(r.score)}</b></td><td class="num"><b>${fmt(r.single_core)}</b></td></tr>`).join("")}</tbody></table></div></div>`;
}

async function viewVersions() {
  let releases = [];
  try {
    const r = await fetch(`https://api.github.com/repos/${REPO}/releases?per_page=100`);
    if (r.ok) releases = (await r.json()).filter((x) => !x.draft && /^v?\d/.test(x.tag_name))
      .map((x) => ({ v: x.tag_name.replace(/^v/, ""), date: (x.published_at || "").slice(0, 10), url: x.html_url }));
  } catch { /* offline or rate limited: the versions with results only */ }
  const count = new Map();
  for (const r of RESULTS) count.set(r.hertzon, (count.get(r.hertzon) || 0) + 1);
  for (const v of count.keys()) if (!releases.some((x) => x.v === v)) releases.push({ v, date: "", url: "" });
  releases.sort((a, b) => vcmp(b.v, a.v));
  const medOf = (v, f) => { const s = RESULTS.filter((r) => r.hertzon === v && r.suite === state.suite).map(f).filter(Boolean); return s.length ? median(s) : null; };
  app.innerHTML = hero("Hertzon versions", "Every release of Hertzon. Versions with benchmark results link to them, with the change against the version before on the same hardware.")
    + `<div class="page"><div class="tbl-wrap"><table><thead><tr><th>Version</th><th>Released</th><th class="num">Results</th>
    <th class="num">Median multi-core</th><th class="num">Median single-core</th><th class="opt"></th></tr></thead><tbody>
    ${releases.map((x) => { const n = count.get(x.v) || 0;
      return `<tr><td>${n ? `<a href="#/version/${esc(x.v)}"><b>${esc(x.v)}</b></a>` : esc(x.v)}</td><td>${esc(x.date)}</td>
      <td class="num">${n || '<span class="muted">none</span>'}</td><td class="num">${fmt(medOf(x.v, (r) => r.score))}</td>
      <td class="num">${fmt(medOf(x.v, (r) => r.single_core))}</td>
      <td class="opt">${x.url ? `<a href="${esc(x.url)}">Release notes</a>` : ""}</td></tr>`; }).join("")}</tbody></table></div></div>`;
}

function viewVersion(v) {
  const here = RESULTS.filter((r) => r.hertzon === v && r.suite === state.suite);
  if (!here.length) { app.innerHTML = `<div class="page"><div class="empty">No results for Hertzon ${esc(v)} yet.</div></div>`; return; }
  const older = [...new Set(RESULTS.filter((r) => r.suite === state.suite && vcmp(r.hertzon, v) < 0).map((r) => r.hertzon))].sort(vcmp);
  const prev = older.pop();
  let cmp = "";
  if (prev) {
    const med = (ver, hw) => { const s = RESULTS.filter((r) => r.hertzon === ver && r.hw_key === hw && r.suite === state.suite && r.score).map((r) => r.score); return s.length ? median(s) : null; };
    const hws = [...new Set(here.map((r) => r.hw_key))].map((hw) => ({ r: here.find((x) => x.hw_key === hw), a: med(prev, hw), b: med(v, hw) })).filter((x) => x.a && x.b);
    cmp = `<h2>Against ${esc(prev)} on the same hardware</h2>` + (hws.length ? `<div class="tbl-wrap"><table><thead><tr><th>System</th><th>Solver</th>
      <th class="num">${esc(prev)}</th><th class="num">${esc(v)}</th><th class="num">Change</th></tr></thead><tbody>
      ${hws.map((x) => { const ch = 100 * (x.b / x.a - 1); return `<tr>${systemCell(x.r)}<td>${esc(x.r.solver)}</td><td class="num">${fmt(x.a)}</td><td class="num">${fmt(x.b)}</td>
        <td class="num ${ch > 3 ? "up" : ch < -3 ? "down" : ""}">${ch > 0 ? "+" : ""}${fmt(ch, 1)} %</td></tr>`; }).join("")}</tbody></table></div>
      <p class="note">Differences below about 5 % are within the spread of repeated runs.</p>`
      : `<p class="muted">No computer has multi-core results for both versions yet.</p>`);
  }
  const saved = state.version;
  state.version = v;
  app.innerHTML = hero(`Hertzon ${esc(v)}`, `${here.length} results from ${new Set(here.map((r) => r.hw_key)).size} systems.`)
    + `<div class="page"><p class="crumbs"><a href="#/versions">Versions</a> › ${esc(v)}</p>${cmp}<h2>Ranking</h2>${modeBar(false)}${rankingTable(filtered())}</div>`;
  state.version = saved;
  bindFilters(() => viewVersion(v));
}

function viewAbout() {
  const s = spec();
  const scal = s.scaling_tests || {};
  app.innerHTML = hero("About Hertzon Bench", `Hertzon is an open-source electromagnetic design suite with a 3D FEM solver and adaptive meshing.
    Hertzon Bench measures how fast a computer solves typical problems with it: a CPU benchmark with Maxwell's equations.`)
    + `<div class="page">
  <div class="cards three">
    <div class="card feature"><span class="ic">${icon("cpu")}</span><h3>Multi-core: the full benchmark</h3><p>Every test of both parts with all cores and the chosen
      matrix solver: a score per test, an ${parts().map((p) => esc(p.title)).join(" and an ")} score and an overall score.</p></div>
    <div class="card feature"><span class="ic">${icon("scale")}</span><h3>Single-core: the scaling benchmark</h3><p>One test per part
      (${parts().map((p) => `${esc(p.title)}: ${esc(s.titles[scal[p.id]] || "")}`).join(", ")}) with 1, 2, 4, ... up to all cores:
      single-core scores, speedup and parallel efficiency.</p></div>
    <div class="card feature"><span class="ic">${icon("check")}</span><h3>Checked results</h3><p>Every result is checked against a reference: the S-parameters,
      and for the spheres the exact Mie solution. A wrong or unconverged result gets no score.</p></div>
  </div>
  <h2>The tests (${esc(state.suite)})</h2><div class="tbl-wrap"><table><thead><tr><th>Part</th><th>Test</th><th class="opt">What it measures</th><th class="num">Reference time</th></tr></thead><tbody>
  ${s.tests.map((t) => `<tr><td>${esc(t.large ? "Large" : partTitle((parts().find((p) => p.tests.includes(t.id)) || {}).id))}</td><td>${esc(s.titles[t.id])}</td>
    <td class="wrap opt">${esc(s.about[t.id])}</td><td class="num">${fmt(t.time, 1)} s</td></tr>`).join("")}</tbody></table></div>
  <h2>How it is measured</h2>
  <p>Every test solves its whole setup once (initial mesh, adaptive passes to convergence, frequency sweep or far field) in a fresh
  solver process with the chosen matrix solver. Score of a test = reference time / measured time × 1000, with the reference computer
  ${esc(s.reference)}. A part score is the geometric mean of its test scores, so no single test dominates, and the overall score is
  the geometric mean of the part scores. The single-core score of a part comes from the 1-core time of its scaling test. Both
  benchmarks and both parts can be run on their own. Scores of different suite versions are not comparable; between Hertzon versions
  the suite stays the same, so the versions page shows whether Hertzon got faster.</p>
  <p>A test counts only when it converged with the chosen matrix solver and a correct result. A test stopped by the memory guard,
  ended for lack of memory, solved by another solver than chosen (e.g. no usable GPU) or not converged keeps its times and is marked
  with the reason; its part then has no score and the run no overall score, while the other part keeps its score. Large tests run
  only on computers with enough memory and have their own score (1000 = ${esc(s.reference_large || "")}).</p>
  <p>Several submissions of the same computer are welcome; the ranking shows their median. The memory bandwidth is measured by
  Hertzon (copying a 256 MB array on one core), because a sparse direct solver depends on it.</p>
  <h2>Privacy</h2>
  <p>Hertzon shows exactly what it sends and sends only after you agree: times, checks and scores, the hardware and software
  description (device model, CPU, memory, GPU, operating system, library versions) and, if you enter them, a display name and a
  comment. Not sent: user or computer name, serial numbers, network addresses, paths, projects. The site stores no IP addresses;
  for the limit of ${10} submissions per day it keeps a salted hash of the address for one day.</p>
  <div class="card del"><h3>Remove a result</h3><p class="sub">With the delete key Hertzon showed after submitting.</p>
  <div class="filters"><label>Result id<input id="del-id"></label><label>Delete key<input id="del-key"></label>
  <label>&nbsp;<button class="btn primary" id="del-btn">Remove</button></label></div><p id="del-msg" class="sub"></p></div></div>`;
  document.getElementById("del-btn").addEventListener("click", async () => {
    const id = document.getElementById("del-id").value.trim(), key = document.getElementById("del-key").value.trim();
    const r = await fetch(`api/result/${encodeURIComponent(id)}`, { method: "DELETE", headers: { "X-Delete-Key": key } });
    document.getElementById("del-msg").textContent = r.ok ? "Removed." : ((await r.json()).error || "Not removed.");
    if (r.ok) RESULTS = null;
  });
}

function icon(name) {
  const p = { cpu: '<rect x="6" y="6" width="12" height="12" rx="2"/><path d="M9 2v4M15 2v4M9 18v4M15 18v4M2 9h4M2 15h4M18 9h4M18 15h4"/>',
    scale: '<path d="M3 20h18"/><path d="M5 17l4-5 4 3 6-8"/><path d="M15 7h4v4"/>', check: '<circle cx="12" cy="12" r="9"/><path d="M8 12.5l2.5 2.5L16 9.5"/>' }[name];
  return `<svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${p}</svg>`;
}

async function route() {
  await load();
  const [, view, arg] = (location.hash || "#/").split("/");
  document.querySelectorAll("[data-nav]").forEach((a) => a.classList.toggle("active",
    a.dataset.nav === ({ "": "ranking", system: "ranking", result: "ranking", version: "versions" }[view || ""] || view)));
  const v = decodeURIComponent(arg || "");
  if (view === "result") await viewResult(v);
  else if (view === "system") viewSystem(v);
  else if (view === "versions") await viewVersions();
  else if (view === "version") viewVersion(v);
  else if (view === "about") viewAbout();
  else viewRanking();
  document.title = "Hertzon Bench" + (view ? ` · ${view[0].toUpperCase()}${view.slice(1)}` : "");
}
let lastView = "";
window.addEventListener("hashchange", () => { const v = location.hash.split("/")[1] || ""; if (v !== lastView) window.scrollTo(0, 0); lastView = v; route(); });
route();
