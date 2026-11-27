// GET /api/results[?suite=...]: every visible result of a suite, without the per-test details (those come
// from /api/result/<id>), newest first. Cached for a minute at the edge.
import { json } from "../_lib/validate.js";

export async function onRequestGet({ request, env }) {
  const url = new URL(request.url);
  const suite = url.searchParams.get("suite");
  const q = suite
    ? env.DB.prepare(`SELECT id, created, suite, hertzon, platform, arch, cpu, gpu, memory_gb, bandwidth, cores,
        solver, score, complete, hw_key, system_key, name, json_extract(data, '$.tests') AS tests,
        json_extract(data, '$.system.device') AS device, json_extract(data, '$.system.device_type') AS device_type,
        json_extract(data, '$.system.os') AS os, json_extract(data, '$.scaling.single_core_score') AS single_core,
        json_extract(data, '$.scaling.parts') AS scaling,
        json_extract(data, '$.parts') AS parts
        FROM results WHERE hidden = 0 AND suite = ? ORDER BY created DESC`).bind(suite)
    : env.DB.prepare(`SELECT id, created, suite, hertzon, platform, arch, cpu, gpu, memory_gb, bandwidth, cores,
        solver, score, complete, hw_key, system_key, name, json_extract(data, '$.tests') AS tests,
        json_extract(data, '$.system.device') AS device, json_extract(data, '$.system.device_type') AS device_type,
        json_extract(data, '$.system.os') AS os, json_extract(data, '$.scaling.single_core_score') AS single_core,
        json_extract(data, '$.scaling.parts') AS scaling,
        json_extract(data, '$.parts') AS parts
        FROM results WHERE hidden = 0 ORDER BY created DESC`);
  const { results } = await q.all();
  for (const r of results) {
    try { r.parts = JSON.parse(r.parts) || {}; } catch { r.parts = {}; }
    // per part only the single-core score and the speedup at all cores, for the single-core ranking
    try {
      const sc = JSON.parse(r.scaling) || {};
      r.single = Object.fromEntries(Object.entries(sc).map(([k, p]) => {
        const top = (p.runs || []).reduce((a, x) => (x.cores > (a ? a.cores : 0) ? x : a), null);
        return [k, { score: p.single_core_score, speedup: top ? top.speedup : null, cores: top ? top.cores : null }];
      }));
    } catch { r.single = {}; }
    delete r.scaling;
    // per test only the id and score: enough for the ranking per test
    try { r.tests = JSON.parse(r.tests).map((t) => ({ id: t.id, score: t.score, total: t.total,
      ...(t.counted === false ? { reason: t.reason } : {}), ...(t.skipped ? { skipped: t.skipped } : {}) })); } catch { r.tests = []; }
  }
  return json({ results }, 200, { "Cache-Control": "public, max-age=60" });
}
