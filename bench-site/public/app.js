// Hertzon Bench comparison site: ranking, single results, systems, versions. Plain JavaScript, no build.
"use strict";

const REPO = "c8501024/Hertzon";
const app = document.getElementById("app");
let SPEC = null;          // suite.json: tests, titles, reference per suite
let RESULTS = null;       // /api/results
const state = { suite: "", version: "", platform: "", arch: "", solver: "", device: "", q: "", test: "", group: true, sort: "score", incomplete: false };

const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const sec = (x) => (x === null || x === undefined ? "" : `${Number(x).toFixed(1)} s`);
const fmt = (x, d = 0) => (x === null || x === undefined || Number.isNaN(x) ? "" : Number(x).toFixed(d));
const date = (t) => new Date(t * 1000).toISOString().slice(0, 10);
const median = (a) => { const s = [...a].sort((x, y) => x - y); const m = s.length >> 1; return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2; };
const vcmp = (a, b) => { const pa = String(a).split(/[.\-]/).map(Number), pb = String(b).split(/[.\-]/).map(Number);
  for (let i = 0; i < Math.max(pa.length, pb.length); i++) { const d = (pa[i] || 0) - (pb[i] || 0); if (d) return d; } return 0; };

async function load() {
  if (!SPEC) SPEC = await (await fetch("suite.json")).json();
  if (!state.suite) state.suite = Object.keys(SPEC).sort().pop();
  if (!RESULTS) {
    try { RESULTS = (await (await fetch("api/results")).json()).results || []; }
    catch { RESULTS = []; }
  }
}

function scoreOf(r) {
  if (!state.test) return r.score;
  if (state.test === "single") return r.single_core;
  const t = (r.tests || []).find((x) => x.id === state.test);
  return t ? t.score : null;
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
    && (state.test || state.incomplete || r.complete)
    && scoreOf(r));
}

// one row per system (same hardware, solver and version): median score, best and number of runs
function grouped(rows) {
  const g = new Map();
  for (const r of rows) {
    const k = r.system_key;
    if (!g.has(k)) g.set(k, []);
    g.get(k).push(r);
  }
  return [...g.values()].map((runs) => {
    const s = runs.map(scoreOf);
    return { ...runs[0], score_med: median(s), score_best: Math.max(...s), runs: runs.length, ids: runs.map((r) => r.id) };
  });
}

function options(values, cur, all) {
  return `<option value="">${all}</option>` + [...new Set(values.filter(Boolean))].sort()
    .map((v) => `<option ${v === cur ? "selected" : ""}>${esc(v)}</option>`).join("");
}

function coreCount(r) {
  const core = SPEC[r.suite].tests.filter((t) => !t.large).map((t) => t.id);
  return [(r.tests || []).filter((t) => core.includes(t.id) && t.score).length, core.length];
}

function systemCell(r) {
  const sub = [r.device, r.gpu].filter(Boolean).join(" · ");
  const [n, m] = coreCount(r);
  return `<td class="wrap"><b>${esc(r.cpu)}</b>${r.name ? `<span class="tag">${esc(r.name)}</span>` : ""}
    ${!r.complete ? `<span class="tag warn" title="Tests that did not count are left out of the score">${n} of ${m} tests</span>` : ""}
    ${sub ? `<div class="sub">${esc(sub)}</div>` : ""}</td>`;
}

function rankingTable(rows) {
  const g = state.group ? grouped(rows) : rows.map((r) => ({ ...r, score_med: scoreOf(r), score_best: scoreOf(r), runs: 1, ids: [r.id] }));
  const key = { score: (r) => -r.score_med, cores: (r) => -r.cores, memory: (r) => -(r.memory_gb || 0),
    bandwidth: (r) => -(r.bandwidth || 0), version: (r) => 0, date: (r) => -r.created }[state.sort] || ((r) => -r.score_med);
  g.sort((a, b) => key(a) - key(b) || (state.sort === "version" ? vcmp(b.hertzon, a.hertzon) : 0) || b.score_med - a.score_med);
  if (!g.length) return `<div class="empty">No results for this selection yet.</div>`;
  const top = Math.max(...g.map((r) => r.score_med));
  const th = (k, t, cls = "") => `<th class="sort ${cls}" data-sort="${k}">${t}${state.sort === k ? " ▾" : ""}</th>`;
  return `<div class="tbl-wrap"><table><thead><tr><th class="num">#</th><th>System</th><th class="opt">OS</th>
    ${th("memory", "Memory", "num opt")}${th("bandwidth", "Bandwidth", "num opt")}${th("cores", "Cores", "num opt")}<th class="opt">Solver</th>
    ${th("version", "Hertzon")}${th("score", state.test === "single" ? "Single-core score" : state.test ? `Score: ${esc(SPEC[state.suite].titles[state.test])}` : "Score")}
    <th class="num">${state.group ? "Runs" : "Date"}</th></tr></thead><tbody>
    ${g.map((r, i) => `<tr>
      <td class="num">${i + 1}</td>${systemCell(r)}
      <td class="opt">${esc(r.platform)}<div class="sub">${esc(r.os || "")}</div></td>
      <td class="num opt">${r.memory_gb ? fmt(r.memory_gb) + " GB" : ""}</td>
      <td class="num opt">${r.bandwidth ? fmt(r.bandwidth) + " GB/s" : ""}</td>
      <td class="num opt">${r.cores}</td><td class="opt">${esc(r.solver)}</td><td>${esc(r.hertzon)}</td>
      <td class="bar"><i style="width:${(100 * r.score_med / top).toFixed(1)}%"></i>
        <b><a href="${r.runs > 1 ? `#/system/${r.system_key}` : `#/result/${r.ids[0]}`}">${fmt(r.score_med)}</a></b>
        ${r.runs > 1 ? `<span class="sub">best ${fmt(r.score_best)}</span>` : ""}</td>
      <td class="num">${state.group ? `<a href="#/system/${r.system_key}">${r.runs}</a>` : date(r.created)}</td></tr>`).join("")}
    </tbody></table></div>`;
}

function filterBar(all) {
  const spec = SPEC[state.suite];
  return `<div class="filters">
    <label>Suite<select data-f="suite">${Object.keys(SPEC).sort().map((s) => `<option ${s === state.suite ? "selected" : ""}>${esc(s)}</option>`).join("")}</select></label>
    <label>Score<select data-f="test"><option value="">Overall (all cores)</option><option value="single" ${state.test === "single" ? "selected" : ""}>Single-core</option>${spec.tests.map((t) => `<option value="${t.id}" ${t.id === state.test ? "selected" : ""}>${esc(spec.titles[t.id])}</option>`).join("")}</select></label>
    <label>Hertzon<select data-f="version">${options(all.map((r) => r.hertzon).sort(vcmp).reverse(), state.version, "All versions")}</select></label>
    <label>OS<select data-f="platform">${options(all.map((r) => r.platform), state.platform, "All")}</select></label>
    <label>Architecture<select data-f="arch">${options(all.map((r) => r.arch), state.arch, "All")}</select></label>
    <label>Device<select data-f="device">${options(all.map((r) => r.device_type), state.device, "All")}</select></label>
    <label>Solver<select data-f="solver">${options(all.map((r) => r.solver), state.solver, "All")}</select></label>
    <label>Search<input data-f="q" value="${esc(state.q)}" placeholder="CPU, GPU, device, name"></label>
    <label>Runs<span class="seg"><button data-inc="0" class="${state.incomplete ? "" : "on"}">Complete</button><button data-inc="1" class="${state.incomplete ? "on" : ""}">Also incomplete</button></span></label>
    <label>Show<span class="seg"><button data-group="1" class="${state.group ? "on" : ""}">Per system</button><button data-group="0" class="${state.group ? "" : "on"}">All runs</button></span></label>
  </div>`;
}

function bindFilters(render) {
  app.querySelectorAll("[data-f]").forEach((el) => el.addEventListener(el.tagName === "INPUT" ? "input" : "change", () => {
    state[el.dataset.f] = el.value;
    if (el.dataset.f === "suite") state.test = "";
    const focus = el.tagName === "INPUT";
    render();
    if (focus) { const i = app.querySelector('[data-f="q"]'); i.focus(); i.setSelectionRange(i.value.length, i.value.length); }
  }));
  app.querySelectorAll("[data-inc]").forEach((b) => b.addEventListener("click", () => { state.incomplete = b.dataset.inc === "1"; render(); }));
  app.querySelectorAll("[data-group]").forEach((b) => b.addEventListener("click", () => { state.group = b.dataset.group === "1"; render(); }));
  app.querySelectorAll("th[data-sort]").forEach((th) => th.addEventListener("click", () => { state.sort = th.dataset.sort; render(); }));
}

function viewRanking() {
  const all = RESULTS.filter((r) => r.suite === state.suite);
  const rows = filtered();
  app.innerHTML = `<h1>${esc(state.suite)}</h1>
    <p class="lead">How fast computers solve ${SPEC[state.suite].tests.filter((t) => !t.large).length} fixed FEM models with Hertzon
    (plus large tests with their own score).
    Higher is better: 1000 is the reference computer (${esc(SPEC[state.suite].reference)}), 2000 is twice as fast.
    ${state.group ? "One row per system: the median of its runs." : ""}
    ${state.incomplete || state.test ? "" : "Only runs in which every test counted; a test that hit the memory guard, fell back to another solver or did not converge is left out of a run's score."}
    ${all.length} results from
    ${new Set(all.map((r) => r.hw_key)).size} systems. <a href="#/about">How it is measured</a></p>
    ${filterBar(all)}${rankingTable(rows)}`;
  bindFilters(viewRanking);
}

function card(title, pairs) {
  const rows = pairs.filter(([, v]) => v !== undefined && v !== null && v !== "" && !(Array.isArray(v) && !v.length));
  return `<div class="card"><h3>${title}</h3><dl class="kv">${rows.map(([k, v]) => `<dt>${esc(k)}</dt><dd>${esc(v)}</dd>`).join("")}</dl></div>`;
}

async function viewResult(id) {
  const r = await fetch(`api/result/${encodeURIComponent(id)}`);
  if (!r.ok) { app.innerHTML = `<div class="empty">Result not found.</div>`; return; }
  const d = await r.json();
  const spec = SPEC[d.suite] || { titles: {}, tests: [] };
  const s = d.system || {}, sw = s.software || {};
  const same = RESULTS.filter((x) => x.suite === d.suite && x.id !== d.id);
  const me = RESULTS.find((x) => x.id === d.id);
  const better = me ? same.filter((x) => x.score > d.score).length : 0;
  app.innerHTML = `<p class="sub"><a href="#/">Ranking</a> › Result ${esc(d.id)}</p>
    <div class="cards">
      <div class="card"><div class="sub">${esc(d.suite)} score</div><div class="big">${fmt(d.score)}</div>
        <p class="sub">${me ? `Rank ${better + 1} of ${same.length + 1} results` : ""} · submitted ${date(d.created)}
        ${d.name ? ` by ${esc(d.name)}` : ""}</p>
        ${d.complete === false ? `<p class="bad">Incomplete: ${d.counted} of ${spec.tests.filter((t) => !t.large).length} tests count; the score is their geometric mean. The reasons are in the table below.</p>` : ""}${d.comment ? `<p>${esc(d.comment)}</p>` : ""}
        ${me ? `<p><a href="#/system/${me.system_key}">All runs of this system</a></p>` : ""}</div>
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
    ${scalingHtml(d, spec)}
    <h2>Tests</h2>
    <div class="tbl-wrap"><table><thead><tr><th>Test</th><th class="num">Total</th><th class="num">Initial mesh</th>
      <th class="num">Adaptive passes</th><th class="num">Sweep</th><th class="num">Passes</th><th class="num">Tetrahedra</th>
      <th class="num">Peak memory</th><th>Result check</th><th class="num">Score</th></tr></thead><tbody>
      ${(d.tests || []).map((t) => t.skipped || t.total === undefined ? `<tr><td>${esc(spec.titles[t.id] || t.id)}</td><td colspan="7"></td>
        <td class="wrap ${t.skipped ? "muted" : "bad"}">${t.skipped ? `Not run: ${esc(t.skipped)}` : `Not counted: ${esc(t.reason)}`}</td><td></td></tr>` :
        `<tr><td>${esc(spec.titles[t.id] || t.id)}${(spec.tests.find((x) => x.id === t.id) || {}).large ? '<span class="tag">large, own score</span>' : ""}</td><td class="num">${sec(t.total)}</td>
        <td class="num">${sec(t.mesh)}</td><td class="num">${sec(t.adaptive)}</td><td class="num">${sec(t.sweep)}</td>
        <td class="num">${t.passes ?? ""}</td><td class="num">${t.tets ?? ""}</td><td class="num">${t.memory_mb ? fmt(t.memory_mb / 1024, 1) + " GB" : ""}</td>
        <td class="wrap ${t.counted !== false && t.check && t.check.ok ? "ok" : "bad"}">${t.counted === false ? `Not counted: ${esc(t.reason)}` : t.check ? (t.check.kind === "rcs_db" ? `${fmt(t.check.value, 2)} dB vs. Mie` : t.check.kind === "rcs_ref_db" ? (t.check.value !== null ? `${fmt(t.check.value, 2)} dB vs. reference` : "ok") : t.check.value !== null ? `|ΔS| ${Number(t.check.value).toExponential(1)}` : "ok") : ""}</td>
        <td class="num"><b>${fmt(t.score)}</b></td></tr>`).join("")}</tbody></table></div>`;
}

function scalingHtml(d, spec) {
  const sc = d.scaling;
  if (!sc || !sc.runs || !sc.runs.length) return "";
  const runs = [...sc.runs].sort((a, b) => a.cores - b.cores);
  const top = Math.max(...runs.map((r) => r.speedup || 1), runs[runs.length - 1].cores);
  return `<h2>Core scaling: ${esc(spec.titles[sc.test] || sc.test)}</h2>
    <p class="sub">The same test solved with fewer cores. Speedup against 1 core; efficiency = speedup / cores.
    ${sc.single_core_score ? `Single-core score <b>${fmt(sc.single_core_score)}</b>.` : ""}</p>
    <div class="tbl-wrap"><table><thead><tr><th class="num">Cores</th><th class="num">Total</th><th class="num">Adaptive passes</th>
    <th class="num">Sweep</th><th>Speedup</th><th class="num">Efficiency</th></tr></thead><tbody>
    ${runs.map((r) => `<tr><td class="num">${r.cores}</td><td class="num">${fmt(r.total, 1)} s</td><td class="num">${fmt(r.adaptive, 1)} s</td>
      <td class="num">${fmt(r.sweep, 1)} s</td><td class="bar"><i style="width:${(100 * (r.speedup || 0) / top).toFixed(1)}%"></i><b>${r.speedup ? fmt(r.speedup, 2) + "×" : ""}</b></td>
      <td class="num">${r.efficiency ? fmt(100 * r.efficiency) + " %" : ""}</td></tr>`).join("")}</tbody></table></div>`;
}

function viewSystem(key) {
  const runs = RESULTS.filter((r) => r.system_key === key).sort((a, b) => b.created - a.created);
  if (!runs.length) { app.innerHTML = `<div class="empty">No results for this system.</div>`; return; }
  const r0 = runs[0], s = runs.map((r) => r.score);
  const other = RESULTS.filter((r) => r.hw_key === r0.hw_key && r.system_key !== key);
  const byVer = new Map();
  for (const r of [...runs, ...other]) { if (!byVer.has(r.hertzon)) byVer.set(r.hertzon, []); byVer.get(r.hertzon).push(r.score); }
  app.innerHTML = `<p class="sub"><a href="#/">Ranking</a> › System</p>
    <h1>${esc(r0.cpu)}</h1><p class="lead">${esc([r0.device, r0.gpu, r0.os, r0.memory_gb && r0.memory_gb + " GB", `${r0.cores} cores`, r0.solver].filter(Boolean).join(" · "))}</p>
    <div class="cards"><div class="card"><div class="sub">Median of ${runs.length} run(s), Hertzon ${esc(r0.hertzon)}</div><div class="big">${fmt(median(s))}</div>
      <p class="sub">best ${fmt(Math.max(...s))}, lowest ${fmt(Math.min(...s))}, spread ${fmt(100 * (Math.max(...s) - Math.min(...s)) / median(s))} %</p></div>
      ${byVer.size > 1 ? `<div class="card"><h3>Same hardware, other Hertzon versions</h3><dl class="kv">${[...byVer.entries()].sort((a, b) => vcmp(b[0], a[0]))
        .map(([v, sc]) => `<dt>${esc(v)}</dt><dd>${fmt(median(sc))} <span class="sub">(${sc.length} run${sc.length > 1 ? "s" : ""})</span></dd>`).join("")}</dl></div>` : ""}</div>
    <h2>Runs</h2><div class="tbl-wrap"><table><thead><tr><th>Date</th><th>Name</th>${SPEC[r0.suite].tests.map((t) => `<th class="num">${esc(SPEC[r0.suite].titles[t.id])}</th>`).join("")}<th class="num">Score</th></tr></thead><tbody>
    ${runs.map((r) => `<tr><td><a href="#/result/${r.id}">${date(r.created)}</a></td><td>${esc(r.name)}</td>
      ${SPEC[r0.suite].tests.map((t) => { const x = (r.tests || []).find((y) => y.id === t.id); return `<td class="num">${x ? fmt(x.score) : ""}</td>`; }).join("")}
      <td class="num"><b>${fmt(r.score)}</b></td></tr>`).join("")}</tbody></table></div>`;
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
  app.innerHTML = `<h1>Hertzon versions</h1><p class="lead">Every release of Hertzon. Versions with benchmark results link to them,
    with the change against the version before on the same hardware.</p>
    <div class="tbl-wrap"><table><thead><tr><th>Version</th><th>Released</th><th class="num">Results</th><th class="num">Median score</th><th></th></tr></thead><tbody>
    ${releases.map((x) => { const n = count.get(x.v) || 0; const sc = RESULTS.filter((r) => r.hertzon === x.v && r.suite === state.suite).map((r) => r.score);
      return `<tr><td>${n ? `<a href="#/version/${esc(x.v)}"><b>${esc(x.v)}</b></a>` : esc(x.v)}</td><td>${esc(x.date)}</td>
      <td class="num">${n || '<span class="muted">none</span>'}</td><td class="num">${sc.length ? fmt(median(sc)) : ""}</td>
      <td>${x.url ? `<a href="${esc(x.url)}">Release notes</a>` : ""}</td></tr>`; }).join("")}</tbody></table></div>`;
}

function viewVersion(v) {
  const here = RESULTS.filter((r) => r.hertzon === v && r.suite === state.suite);
  if (!here.length) { app.innerHTML = `<div class="empty">No results for Hertzon ${esc(v)} yet.</div>`; return; }
  const older = [...new Set(RESULTS.filter((r) => r.suite === state.suite && vcmp(r.hertzon, v) < 0).map((r) => r.hertzon))].sort(vcmp);
  const prev = older.pop();
  let cmp = "";
  if (prev) {
    const med = (ver, hw) => { const s = RESULTS.filter((r) => r.hertzon === ver && r.hw_key === hw && r.suite === state.suite).map((r) => r.score); return s.length ? median(s) : null; };
    const hws = [...new Set(here.map((r) => r.hw_key))].map((hw) => ({ r: here.find((x) => x.hw_key === hw), a: med(prev, hw), b: med(v, hw) })).filter((x) => x.a);
    cmp = `<h2>Against ${esc(prev)} on the same hardware</h2>` + (hws.length ? `<div class="tbl-wrap"><table><thead><tr><th>System</th><th>Solver</th>
      <th class="num">${esc(prev)}</th><th class="num">${esc(v)}</th><th class="num">Change</th></tr></thead><tbody>
      ${hws.map((x) => { const ch = 100 * (x.b / x.a - 1); return `<tr>${systemCell(x.r)}<td>${esc(x.r.solver)}</td><td class="num">${fmt(x.a)}</td><td class="num">${fmt(x.b)}</td>
        <td class="num ${ch > 3 ? "up" : ch < -3 ? "down" : ""}">${ch > 0 ? "+" : ""}${fmt(ch, 1)} %</td></tr>`; }).join("")}</tbody></table></div>
      <p class="sub">Differences below about 5 % are within the spread of repeated runs.</p>`
      : `<p class="muted">No computer has results for both versions yet.</p>`);
  }
  const saved = state.version;
  state.version = v;
  app.innerHTML = `<p class="sub"><a href="#/versions">Versions</a> › ${esc(v)}</p><h1>Hertzon ${esc(v)}</h1>
    <p class="lead">${here.length} results from ${new Set(here.map((r) => r.hw_key)).size} systems.</p>${cmp}<h2>Ranking</h2>${rankingTable(filtered())}`;
  state.version = saved;
}

function viewAbout() {
  const spec = SPEC[state.suite];
  app.innerHTML = `<h1>About Hertzon Bench</h1>
  <p class="lead">Hertzon is an open-source electromagnetic design suite with a 3D FEM solver and adaptive meshing.
  Hertzon Bench measures how fast a computer solves typical problems with it.</p>
  <h2>The tests (${esc(state.suite)})</h2><div class="tbl-wrap"><table><thead><tr><th>Test</th><th>What it measures</th><th class="num">Reference time</th></tr></thead><tbody>
  ${spec.tests.map((t) => `<tr><td>${esc(spec.titles[t.id])}</td><td class="wrap">${esc(spec.about[t.id])}</td><td class="num">${fmt(t.time, 1)} s</td></tr>`).join("")}</tbody></table></div>
  <h2>How it is measured</h2>
  <p>Every test solves its whole setup once (initial mesh, adaptive passes to convergence, frequency sweep or far field) in a fresh
  solver process with all cores and the chosen matrix solver. The result is checked against a reference: the S-parameters,
  and for the sphere the exact Mie solution. A run with a wrong result gets no score.</p>
  <p>Score of a test = reference time / measured time × 1000, with the reference computer ${esc(spec.reference)}.
  The overall score is the geometric mean of the test scores, so no single test dominates. Scores of different suite versions
  are not comparable; between Hertzon versions the suite stays the same, so the versions page shows whether Hertzon got faster.</p>
  <p>A test counts only when it converged with the chosen matrix solver and a correct result. A test stopped by the memory
  guard, ended for lack of memory, solved by another solver than chosen (e.g. no usable GPU) or not converged is left out
  and marked with the reason; the run is then marked incomplete and the ranking shows only complete runs unless you ask for
  the others. Large tests run only on computers with enough memory and have their own score (1000 = ${esc(spec.reference_large || "")}). The core scaling solves the
  ${esc(spec.titles[spec.scaling_test] || "")} with 1, 2, 4, ... cores; its 1-core time gives the single-core score.</p>
  <p>Several submissions of the same computer are welcome; the ranking shows their median. The memory bandwidth is measured
  by Hertzon (copying a 256 MB array on one core), because a sparse direct solver depends on it.</p>
  <h2>Privacy</h2>
  <p>Hertzon shows exactly what it sends and sends only after you agree: times, checks and scores, the hardware and software
  description (device model, CPU, memory, GPU, operating system, library versions) and, if you enter them, a display name and a
  comment. Not sent: user or computer name, serial numbers, network addresses, paths, projects. The site stores no IP addresses;
  for the limit of ${10} submissions per day it keeps a salted hash of the address for one day.</p>
  <p>To remove your result, use the delete key Hertzon showed after submitting:</p>
  <div class="filters"><label>Result id<input id="del-id"></label><label>Delete key<input id="del-key"></label>
  <label>&nbsp;<span class="seg"><button id="del-btn">Remove</button></span></label></div><p id="del-msg" class="sub"></p>`;
  document.getElementById("del-btn").addEventListener("click", async () => {
    const id = document.getElementById("del-id").value.trim(), key = document.getElementById("del-key").value.trim();
    const r = await fetch(`api/result/${encodeURIComponent(id)}`, { method: "DELETE", headers: { "X-Delete-Key": key } });
    document.getElementById("del-msg").textContent = r.ok ? "Removed." : ((await r.json()).error || "Not removed.");
    if (r.ok) RESULTS = null;
  });
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
window.addEventListener("hashchange", route);
route();
