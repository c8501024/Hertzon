// POST /api/submit: store one Hertzon Bench result. Answers {id, url, delete_key}.
import { validate, sha256, randomId, json } from "../_lib/validate.js";

const PER_DAY = 10;   // submissions per address and day

export async function onRequestPost({ request, env }) {
  const raw = await request.text();
  const v = validate(raw);
  if (v.error) return json({ error: v.error }, 400);
  const d = v.data;
  const now = Math.floor(Date.now() / 1000);

  // rate limit: a salted hash of the address and the day, never the address itself; kept one day
  const ip = request.headers.get("CF-Connecting-IP") || "";
  const day = Math.floor(now / 86400);
  const ipHash = await sha256(`${env.RATE_SALT || "hertzon-bench"}|${day}|${ip}`);
  await env.DB.prepare("DELETE FROM rate WHERE ts < ?").bind(now - 86400).run();
  const { n } = await env.DB.prepare("SELECT COUNT(*) AS n FROM rate WHERE ip_hash = ?").bind(ipHash).first();
  if (n >= PER_DAY) return json({ error: `At most ${PER_DAY} submissions per day.` }, 429);

  const dup = await env.DB.prepare("SELECT id FROM results WHERE run_id = ?").bind(d.run_id).first();
  const origin = new URL(request.url).origin;
  if (dup) return json({ id: dup.id, url: `${origin}/#/result/${dup.id}`, delete_key: "", duplicate: true });

  const sys = d.system;
  const gpu = (sys.gpus || []).map((g) => g.name).filter(Boolean).join(", ").slice(0, 200);
  const hw = [sys.platform, sys.arch, sys.cpu, sys.logical_cores, Math.round(sys.memory_gb || 0), gpu, d.cores, d.solver]
    .join("|").toLowerCase();
  const hwKey = (await sha256(hw)).slice(0, 16);
  const systemKey = (await sha256(`${hw}|${v.version}`)).slice(0, 16);
  const id = randomId(8);
  const deleteKey = randomId(20);
  d.name = typeof d.name === "string" ? d.name.replace(/[<>]/g, "").trim().slice(0, 40) : "";
  d.comment = typeof d.comment === "string" ? d.comment.replace(/[<>]/g, "").trim().slice(0, 200) : "";

  await env.DB.batch([
    env.DB.prepare(
      `INSERT INTO results (id, created, run_id, suite, hertzon, platform, arch, cpu, gpu, memory_gb, bandwidth,
        cores, solver, score, hw_key, system_key, name, data, delete_hash)
       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`
    ).bind(id, now, d.run_id, d.suite, v.version, sys.platform, String(sys.arch || "").slice(0, 20),
      String(sys.cpu).slice(0, 120), gpu, sys.memory_gb || null, sys.memory_bandwidth_gbs || null,
      d.cores, String(d.solver || "Auto").slice(0, 40), v.score, hwKey, systemKey, d.name,
      JSON.stringify(d), await sha256(deleteKey)),
    env.DB.prepare("INSERT INTO rate (ip_hash, ts) VALUES (?, ?)").bind(ipHash, now),
  ]);
  return json({ id, url: `${origin}/#/result/${id}`, delete_key: deleteKey }, 201);
}

export function onRequestGet() {
  return json({ error: "Use POST from Hertzon (Tools > Solver Benchmark > Hertzon Bench > Submit Results)." }, 405);
}
