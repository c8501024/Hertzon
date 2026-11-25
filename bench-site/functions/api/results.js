// GET /api/results[?suite=...]: every visible result of a suite, without the per-test details (those come
// from /api/result/<id>), newest first. Cached for a minute at the edge.
import { json } from "../_lib/validate.js";

export async function onRequestGet({ request, env }) {
  const url = new URL(request.url);
  const suite = url.searchParams.get("suite");
  const q = suite
    ? env.DB.prepare(`SELECT id, created, suite, hertzon, platform, arch, cpu, gpu, memory_gb, bandwidth, cores,
        solver, score, hw_key, system_key, name, json_extract(data, '$.tests') AS tests,
        json_extract(data, '$.system.device') AS device, json_extract(data, '$.system.device_type') AS device_type,
        json_extract(data, '$.system.os') AS os FROM results WHERE hidden = 0 AND suite = ? ORDER BY created DESC`).bind(suite)
    : env.DB.prepare(`SELECT id, created, suite, hertzon, platform, arch, cpu, gpu, memory_gb, bandwidth, cores,
        solver, score, hw_key, system_key, name, json_extract(data, '$.tests') AS tests,
        json_extract(data, '$.system.device') AS device, json_extract(data, '$.system.device_type') AS device_type,
        json_extract(data, '$.system.os') AS os FROM results WHERE hidden = 0 ORDER BY created DESC`);
  const { results } = await q.all();
  for (const r of results) {
    // per test only the id and score: enough for the ranking per test
    try { r.tests = JSON.parse(r.tests).map((t) => ({ id: t.id, score: t.score, total: t.total })); } catch { r.tests = []; }
  }
  return json({ results }, 200, { "Cache-Control": "public, max-age=60" });
}
