// Search over the documentation (search-index.json, built by site/build.py) and the "On this page" highlight.
(function () {
  const P = window.HZ_PREFIX || "";
  let index = null, sel = 0, hits = [];
  const $ = (id) => document.getElementById(id);
  const esc = (s) => s.replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

  async function load() {
    if (!index) index = await (await fetch(P + "search-index.json")).json();
    return index;
  }
  function snippet(text, terms) {
    const low = text.toLowerCase();
    let pos = -1;
    for (const t of terms) { pos = low.indexOf(t); if (pos >= 0) break; }
    const start = Math.max(0, pos - 50);
    let s = esc((start ? "…" : "") + text.slice(start, start + 160) + "…");
    for (const t of terms) s = s.replace(new RegExp("(" + t.replace(/[.*+?^${}()|[\]\\]/g, "\\$&") + ")", "ig"), "<mark>$1</mark>");
    return s;
  }
  async function run(q) {
    const terms = q.toLowerCase().split(/\s+/).filter((t) => t.length > 1);
    const res = $("search-res");
    if (!terms.length) { res.innerHTML = ""; hits = []; return; }
    const idx = await load();
    hits = [];
    for (const e of idx) {
      const t = e.t.toLowerCase(), x = e.x.toLowerCase();
      let score = 0, all = true;
      for (const w of terms) {
        const inT = t.includes(w), inX = x.includes(w);
        if (!inT && !inX) { all = false; break; }
        score += (inT ? 10 : 0) + (inX ? 1 : 0);
      }
      if (all) hits.push([score, e]);
    }
    hits.sort((a, b) => b[0] - a[0]);
    hits = hits.slice(0, 30).map((h) => h[1]);
    sel = 0;
    res.innerHTML = hits.length ? hits.map((e, i) =>
      `<li><a href="${P + e.u}"${i === 0 ? ' class="sel"' : ""}><b>${esc(e.t)}</b><small>${esc(e.p)} · ${snippet(e.x, terms)}</small></a></li>`
    ).join("") : '<li style="padding:12px;color:var(--muted)">Nothing found</li>';
  }
  function move(d) {
    const links = $("search-res").querySelectorAll("a");
    if (!links.length) return;
    links[sel].classList.remove("sel");
    sel = (sel + d + links.length) % links.length;
    links[sel].classList.add("sel");
    links[sel].scrollIntoView({ block: "nearest" });
  }
  window.hzSearch = {
    open() { const s = $("search"); s.hidden = false; const i = $("search-q"); i.focus(); i.select(); load(); },
    close() { $("search").hidden = true; },
  };
  document.addEventListener("keydown", (ev) => {
    const open = !$("search").hidden;
    if (!open && (ev.key === "/" || (ev.key === "k" && (ev.ctrlKey || ev.metaKey))) &&
        !/input|textarea/i.test(document.activeElement.tagName)) { ev.preventDefault(); hzSearch.open(); }
    else if (open && ev.key === "Escape") hzSearch.close();
    else if (open && ev.key === "ArrowDown") { ev.preventDefault(); move(1); }
    else if (open && ev.key === "ArrowUp") { ev.preventDefault(); move(-1); }
    else if (open && ev.key === "Enter") { const a = $("search-res").querySelectorAll("a")[sel]; if (a) location.href = a.href; }
  });
  document.addEventListener("DOMContentLoaded", () => {
    $("search-q").addEventListener("input", (ev) => run(ev.target.value));
    // highlight the current section in "On this page"
    const links = [...document.querySelectorAll(".toc a")];
    if (!links.length) return;
    const heads = links.map((a) => document.getElementById(decodeURIComponent(a.hash.slice(1)))).filter(Boolean);
    const onScroll = () => {
      let cur = heads[0];
      for (const h of heads) if (h.getBoundingClientRect().top < 120) cur = h;
      links.forEach((a) => a.classList.toggle("on", cur && a.hash.slice(1) === cur.id));
    };
    document.addEventListener("scroll", onScroll, { passive: true });
    onScroll();
    // close the mobile menu after picking a page
    document.querySelectorAll(".side a").forEach((a) => a.addEventListener("click", () => document.body.classList.remove("nav-open")));
  });
})();
