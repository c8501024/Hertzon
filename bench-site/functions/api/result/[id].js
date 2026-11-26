// GET /api/result/<id>: one result with all details.
// DELETE /api/result/<id> with header X-Delete-Key: the submitter removes it (hidden, no longer listed).
import { sha256, json } from "../../_lib/validate.js";

export async function onRequestGet({ params, env }) {
  const r = await env.DB.prepare("SELECT id, created, data FROM results WHERE id = ? AND hidden = 0")
    .bind(params.id).first();
  if (!r) return json({ error: "Not found." }, 404);
  return json({ id: r.id, created: r.created, ...JSON.parse(r.data) }, 200, { "Cache-Control": "public, max-age=300" });
}

export async function onRequestDelete({ params, request, env }) {
  const key = request.headers.get("X-Delete-Key") || "";
  if (!key) return json({ error: "Delete key missing." }, 400);
  const r = await env.DB.prepare("SELECT delete_hash FROM results WHERE id = ?").bind(params.id).first();
  if (!r || r.delete_hash !== (await sha256(key))) return json({ error: "Wrong id or key." }, 403);
  await env.DB.prepare("UPDATE results SET hidden = 1 WHERE id = ?").bind(params.id).run();
  return json({ ok: true });
}
