// Checks a submission before it is stored: structure, value ranges, the suite's models (by their file hash)
// and plausible meshes. It cannot prove that times are honest; it keeps out broken runs, other models,
// edited files and junk. SUITES comes from suite.js, written by make_suite_spec.py from the Hertzon suite.
import { SUITES } from "./suite.js";

const MAX_BYTES = 64 * 1024;

function str(v, max) {
  return typeof v === "string" ? v.replace(/[\u0000-\u001f<>]/g, "").trim().slice(0, max) : "";
}
function num(v, lo, hi) {
  return typeof v === "number" && Number.isFinite(v) && v >= lo && v <= hi;
}

export function validate(raw) {
  if (raw.length > MAX_BYTES) return { error: "Submission too large." };
  let d;
  try { d = JSON.parse(raw); } catch { return { error: "Not JSON." }; }
  if (!d || typeof d !== "object") return { error: "Not an object." };
  if (d.schema !== 1) return { error: "Unknown format; update Hertzon." };
  const spec = SUITES[d.suite];
  if (!spec) return { error: `Unknown suite '${str(d.suite, 40)}'; update Hertzon.` };
  if (!/^[0-9a-f]{32}$/.test(d.run_id || "")) return { error: "Missing run id." };
  if (!num(d.cores, 1, 1024)) return { error: "Cores out of range." };
  if (d.score !== null && d.score !== undefined && !num(d.score, 1, 1e7)) return { error: "Overall score out of range." };
  const sys = d.system || {};
  if (!["Windows", "macOS", "Linux"].includes(sys.platform)) return { error: "Unknown platform." };
  if (!str(sys.cpu, 120)) return { error: "Missing CPU." };
  if (!num(sys.logical_cores, 1, 4096)) return { error: "Missing core count." };
  if (sys.memory_gb !== undefined && !num(sys.memory_gb, 0.5, 65536)) return { error: "Memory out of range." };
  const ver = str((sys.software || {}).hertzon, 20);
  if (!/^\d+\.\d+\.\d+([.\-+][0-9A-Za-z.\-]+)?$/.test(ver)) return { error: "Missing Hertzon version." };
  if (!Array.isArray(d.tests) || d.tests.length > spec.tests.length) return { error: "Unknown tests." };

  const byId = Object.fromEntries(d.tests.map((t) => [t && t.id, t]));
  d.parts_sent = d.parts && typeof d.parts === "object" ? d.parts : {};
  // the parts (RF, RCS) that ran: every test of such a part must be there; a part that did not run has none
  const parts = spec.parts || [{ id: "all", title: "All", tests: spec.tests.filter((t) => !t.large).map((t) => t.id) }];
  const run = parts.filter((p) => p.tests.some((id) => byId[id]));
  if (!run.length) return { error: "No part of the suite ran." };
  const partOf = Object.fromEntries(parts.flatMap((p) => p.tests.map((id) => [id, p.id])));
  const scores = {};
  let counted = 0;
  for (const ref of spec.tests) {
    const t = byId[ref.id];
    if (!t) {
      if (ref.large || !run.some((p) => p.id === partOf[ref.id])) continue;   // large tests are optional
      return { error: `Test '${ref.id}' missing.` };
    }
    if (!ref.hashes.includes(t.file_hash)) return { error: `Test '${ref.id}' used another model.` };
    if (t.skipped !== undefined) {
      if (!ref.large || !str(t.skipped, 300)) return { error: `Test '${ref.id}' cannot be skipped.` };
      t.score = null;
      continue;
    }
    if (t.counted === false) {
      // not counted (memory guard, solver fallback, no convergence, wrong result): kept with its reason
      if (!str(t.reason, 400)) return { error: `Test '${ref.id}': reason missing.` };
      if (t.score) return { error: `Test '${ref.id}' does not count but has a score.` };
      t.score = null;
      continue;
    }
    if (!num(t.total, 0.1, 86400)) return { error: `Test '${ref.id}': time out of range.` };
    for (const k of ["start", "mesh", "adaptive", "sweep"])
      if (t[k] !== undefined && !num(t[k], 0, t.total + 1)) return { error: `Test '${ref.id}': ${k} out of range.` };
    if (!num(t.passes, 1, 100)) return { error: `Test '${ref.id}': passes out of range.` };
    if (!num(t.tets, ref.tets[0], ref.tets[1])) return { error: `Test '${ref.id}': mesh size implausible.` };
    if (t.memory_mb !== undefined && !num(t.memory_mb, 1, 4e6)) return { error: `Test '${ref.id}': memory out of range.` };
    if (t.converged !== true) return { error: `Test '${ref.id}' did not converge but counts.` };
    if (!t.check || t.check.ok !== true) return { error: `Test '${ref.id}': result check failed.` };
    // the score is recomputed here, not taken from the submission
    const s = Math.round((1000 * ref.time) / t.total);
    if (Math.abs(s - (t.score || 0)) > Math.max(2, 0.01 * s)) return { error: `Test '${ref.id}': score does not match its time.` };
    if (!ref.large) { scores[ref.id] = s; counted++; }
  }
  // a part has a score only when every one of its tests counts; the overall score only when every part has one
  const gmean = (xs) => Math.round(Math.exp(xs.reduce((a, s) => a + Math.log(s), 0) / xs.length));
  d.parts = {};
  for (const p of run) {
    const xs = p.tests.map((id) => scores[id]).filter(Boolean);
    const sc = xs.length === p.tests.length ? gmean(xs) : null;
    const sent = ((d.parts_sent || {})[p.id] || {}).score;
    if (sc && sent && Math.abs(sc - sent) > Math.max(2, 0.01 * sc)) return { error: `${p.title} score does not match its tests.` };
    d.parts[p.id] = { score: sc, counted: xs.length, tests: p.tests.length, complete: sc !== null };
  }
  delete d.parts_sent;
  if (!Object.values(d.parts).some((p) => p.score)) return { error: "No part has a score." };
  d.complete = run.length === parts.length && Object.values(d.parts).every((p) => p.complete);
  const score = d.complete ? gmean(Object.values(d.parts).map((p) => p.score)) : null;
  if (score && d.score && Math.abs(score - d.score) > Math.max(2, 0.01 * score)) return { error: "Overall score does not match the tests." };
  d.score = score;
  d.parts_run = run.map((p) => p.id);
  // core scaling (optional): the scaling test with fewer cores; the single-core score is recomputed
  if (d.scaling !== undefined) {
    const sc = d.scaling;
    if (!sc || sc.test !== spec.scaling_test || !Array.isArray(sc.runs) || sc.runs.length > 16) return { error: "Bad core scaling." };
    for (const r of sc.runs)
      if (!num(r.cores, 1, 1024) || !num(r.total, 0.1, 86400)) return { error: "Bad core scaling run." };
    const one = sc.runs.find((r) => r.cores === 1);
    const ref1 = (spec.single_core || {})[sc.test];
    const main = byId[sc.test];
    sc.single_core_score = one && ref1 && main && main.counted !== false ? Math.round((1000 * ref1) / one.total) : null;
  }
  d.counted = counted;
  return { data: d, score, version: ver, complete: d.complete };
}

export async function sha256(text) {
  const buf = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(text));
  return [...new Uint8Array(buf)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

export function randomId(n) {
  const abc = "abcdefghijkmnpqrstuvwxyz23456789";
  const b = crypto.getRandomValues(new Uint8Array(n));
  return [...b].map((x) => abc[x % abc.length]).join("");
}

export function json(body, status = 200, extra = {}) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json; charset=utf-8", "Cache-Control": "no-store", ...extra },
  });
}
