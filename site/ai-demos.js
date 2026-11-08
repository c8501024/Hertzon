/* Hertzon website: animated replays of AI assistants driving Hertzon through its MCP server.
   Every duration, run count and result shown comes from a real run (data in ai-demos-data.js); the
   animation only compresses the wall-clock time. */
(function () {
  "use strict";
  var NS = "http://www.w3.org/2000/svg";
  var reduce = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  function el(tag, cls, html) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (html != null) e.innerHTML = html;
    return e;
  }
  function svg(tag, attrs) {
    var e = document.createElementNS(NS, tag);
    for (var k in attrs) e.setAttribute(k, attrs[k]);
    return e;
  }
  function fmtClock(min) {
    var h = Math.floor(min / 60), m = Math.floor(min % 60), s = Math.floor((min * 60) % 60);
    return h ? h + " h " + String(m).padStart(2, "0") + " min" : m + " min " + String(s).padStart(2, "0") + " s";
  }

  /* ---------- model view: shapes in model units, y up */
  function View(host, demo) {
    var v = demo.view, w = v.x1 - v.x0, h = v.y1 - v.y0;
    this.demo = demo;
    this.root = svg("svg", { viewBox: v.x0 + " " + (-v.y1) + " " + w + " " + h, "class": "aid-svg",
                             preserveAspectRatio: "xMidYMid meet", role: "img",
                             "aria-label": demo.viewLabel || "Model" });
    this.g = svg("g", { transform: "scale(1,-1)" });
    this.root.appendChild(this.g);
    this.shapes = {};
    host.appendChild(this.root);
  }
  View.prototype.show = function (id) {
    var s = this.demo.shapes[id];
    if (!s || this.shapes[id]) return;
    var n;
    if (s.rect) {
      var r = s.rect;
      n = svg("rect", { x: r[0], y: r[1], width: r[2] - r[0], height: r[3] - r[1] });
    } else if (s.poly) {
      n = svg("polygon", { points: s.poly.map(function (p) { return p.join(","); }).join(" ") });
    } else if (s.circle) {
      n = svg("circle", { cx: s.circle[0], cy: s.circle[1], r: s.circle[2] });
    } else if (s.line) {
      n = svg("polyline", { points: s.line.map(function (p) { return p.join(","); }).join(" ") });
    }
    n.setAttribute("class", "aid-s " + (s.cls || ""));
    this.g.appendChild(n);
    this.shapes[id] = n;
    requestAnimationFrame(function () { requestAnimationFrame(function () { n.classList.add("in"); }); });
  };
  View.prototype.hide = function (id) {
    var n = this.shapes[id];
    if (!n) return;
    delete this.shapes[id];
    n.classList.remove("in");
    setTimeout(function () { n.remove(); }, 400);
  };
  View.prototype.clear = function () {
    for (var k in this.shapes) this.shapes[k].remove();
    this.shapes = {};
  };

  /* ---------- result plot of the end card: p.data is one trace; p.series several, each {label, data, cls, dots};
     a null in a trace's data breaks the line; xticks are numbers or [position, label] */
  function plot(p) {
    var series = p.series || [{data: p.data}];
    var leg = p.series && p.series.length > 1;
    var W = 320, H = p.h || 150, L = 34, R = 8, T = leg ? 24 : 8, B = 26;
    var all = [].concat.apply([], series.map(function (sr) { return sr.data.filter(Boolean); }));
    var xs = all.map(function (d) { return d[0]; });
    var x0 = p.x ? p.x[0] : Math.min.apply(null, xs), x1 = p.x ? p.x[1] : Math.max.apply(null, xs);
    var y0 = p.y[0], y1 = p.y[1];
    function X(x) { return L + (x - x0) / (x1 - x0) * (W - L - R); }
    function Y(y) { return T + (y1 - y) / (y1 - y0) * (H - T - B); }
    function cy(y) { return Y(Math.max(y0, Math.min(y1, y))); }
    var s = svg("svg", { viewBox: "0 0 " + W + " " + H, "class": "aid-plot", role: "img", "aria-label": p.label });
    (p.bands || []).forEach(function (b) {
      s.appendChild(svg("rect", { x: X(b[0]), y: T, width: X(b[1]) - X(b[0]), height: H - T - B, "class": "band" }));
    });
    (p.yticks || []).forEach(function (t) {
      s.appendChild(svg("line", { x1: L, x2: W - R, y1: Y(t), y2: Y(t), "class": "grid" }));
      var tx = svg("text", { x: L - 5, y: Y(t) + 3.5, "text-anchor": "end" });
      tx.textContent = t;
      s.appendChild(tx);
    });
    (p.xticks || []).forEach(function (t) {
      var pos = t.length ? t[0] : t;
      var tx = svg("text", { x: X(pos), y: H - B + 14, "text-anchor": "middle" });
      tx.textContent = t.length ? t[1] : t;
      s.appendChild(tx);
    });
    (p.hlines || []).forEach(function (t) {
      s.appendChild(svg("line", { x1: L, x2: W - R, y1: Y(t), y2: Y(t), "class": "ref" }));
    });
    series.forEach(function (sr) {
      var d = "", pen = "M";
      sr.data.forEach(function (q) {
        if (!q) { pen = "M"; return; }
        d += pen + X(q[0]).toFixed(1) + " " + cy(q[1]).toFixed(1);
        pen = "L";
      });
      s.appendChild(svg("path", { d: d, "class": "trace " + (sr.cls || "") }));
      if (sr.dots) sr.data.forEach(function (q) {
        if (q) s.appendChild(svg("circle", { cx: X(q[0]).toFixed(1), cy: cy(q[1]).toFixed(1), r: 2.4, "class": "dot " + (sr.cls || "") }));
      });
    });
    if (leg) {                          // legend in one row above the plot, right-aligned
      var lx = W - R;
      p.series.slice().reverse().forEach(function (sr) {
        var tx = svg("text", { x: lx, y: 9.5, "text-anchor": "end", "class": "leg" });
        tx.textContent = sr.label;
        s.appendChild(tx);
        lx -= sr.label.length * 5.1 + 4;
        s.appendChild(svg("line", { x1: lx - 14, x2: lx, y1: 6, y2: 6, "class": "trace " + (sr.cls || "") }));
        lx -= 24;
      });
    }
    var cap = svg("text", { x: W - R, y: H - 2, "text-anchor": "end", "class": "cap" });
    cap.textContent = p.xlabel;
    s.appendChild(cap);
    var cyl = svg("text", { x: 2, y: leg ? 2 : T + 2, "class": "cap", "dominant-baseline": "hanging" });
    cyl.textContent = p.ylabel;
    s.appendChild(cyl);
    return s;
  }

  /* ---------- player */
  function Player(host, demos) {
    this.host = host;
    this.demos = demos;
    this.timers = [];
    var tabs = el("div", "aid-tabs");
    tabs.setAttribute("role", "tablist");
    var self = this;
    demos.forEach(function (d, i) {
      var b = el("button", "aid-tab", "<b>" + d.tab + "</b><span>" + d.tabSub + "</span>");
      b.type = "button";
      b.setAttribute("role", "tab");
      b.addEventListener("click", function () { self.select(i, true); });
      tabs.appendChild(b);
    });
    this.tabs = tabs;
    host.appendChild(tabs);

    var win = el("div", "aid-win");
    win.innerHTML =
      '<div class="aid-bar"><span class="dots"><i></i><i></i><i></i></span><span class="aid-title"></span>' +
      '<span class="aid-clock" aria-live="off"></span></div>' +
      '<div class="aid-body"><div class="aid-chat"><div class="aid-log"></div></div>' +
      '<div class="aid-right"><div class="aid-canvas"></div><div class="aid-note"></div>' +
      '<div class="aid-status"><span class="aid-runs"></span><span class="aid-meter"><i></i></span>' +
      '<span class="aid-score"></span></div></div></div>' +
      '<div class="aid-card" hidden></div>' +
      '<button class="aid-replay" type="button" aria-label="Replay">' +
      '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 12a9 9 0 1 0 3-6.7L3 8"/><path d="M3 3v5h5"/></svg> Replay</button>';
    host.appendChild(win);
    this.win = win;
    this.q = function (s) { return win.querySelector(s); };
    this.q(".aid-replay").addEventListener("click", function () { self.select(self.cur, true); });
    this.cur = 0;
  }
  Player.prototype.stop = function () {
    if (this.video) { this.video.pause(); this.video = null; }
    this.timers.forEach(clearTimeout);
    this.timers = [];
    cancelAnimationFrame(this.raf);
  };
  Player.prototype.at = function (sec, fn) { this.timers.push(setTimeout(fn, sec * 1000)); };
  Player.prototype.select = function (i, play) {
    this.stop();
    this.cur = i;
    this.view = null;
    var d = this.demos[i], q = this.q, self = this;
    Array.prototype.forEach.call(this.tabs.children, function (b, k) {
      b.classList.toggle("on", k === i);
      b.setAttribute("aria-selected", k === i ? "true" : "false");
    });
    q(".aid-title").textContent = d.title;
    q(".aid-log").innerHTML = "";
    q(".aid-canvas").innerHTML = "";
    q(".aid-note").textContent = d.viewNote || "";
    q(".aid-card").hidden = true;
    q(".aid-card").innerHTML = "";
    this.win.classList.remove("done");
    this.win.classList.toggle("video", !!d.video);
    this.setStatus(0, null, d);
    q(".aid-clock").textContent = "0 min 00 s";
    if (d.video) { this.selectVideo(d, play); return; }
    this.view = new View(q(".aid-canvas"), d);
    if (!play) return;
    if (reduce) { this.finish(d, true); return; }

    // timeline: each step gets its own share of the animation, the clock follows the real minutes
    var t = 0.4, prevMin = 0, prevT = t;
    var keys = [[0, 0]];
    d.steps.forEach(function (s) {
      var dur = s.dur != null ? s.dur : (s.who === "user" ? 2.2 : s.tool ? 0.9 : 1.6);
      self.at(t, function () { self.step(s, d); });
      if (s.m != null) { keys.push([t, s.m]); prevMin = s.m; prevT = t; }
      t += dur;
    });
    keys.push([t, d.totalMin]);
    var start = performance.now();
    function tick(now) {
      var e = (now - start) / 1000, m = d.totalMin;
      for (var k = 1; k < keys.length; k++) {
        if (e < keys[k][0]) {
          var a = keys[k - 1], b = keys[k];
          m = a[1] + (b[1] - a[1]) * (e - a[0]) / Math.max(1e-6, b[0] - a[0]);
          break;
        }
      }
      q(".aid-clock").textContent = fmtClock(m);
      if (e < t) self.raf = requestAnimationFrame(tick);
    }
    this.raf = requestAnimationFrame(tick);
    this.at(t + 0.3, function () { self.finish(d, false); });
  };
  /* a screen recording of the real Hertzon window, sped up; the log follows the recording's own clock
     (steps carry t = real seconds since the recording started, the prompt was sent at d.t0;
     building the model plays slower than the solver runs, see d.segments) */
  Player.prototype.selectVideo = function (d, play) {
    var self = this, q = this.q;
    var v = el("video");
    v.muted = true;
    v.playsInline = true;
    v.preload = "auto";
    v.setAttribute("aria-label", d.viewLabel || "Screen recording of the Hertzon window");
    v.poster = d.poster || "";
    [].concat(d.video).forEach(function (src) {
      var so = el("source");
      so.src = src;
      so.type = /\.webm$/.test(src) ? "video/webm" : "video/mp4";
      v.appendChild(so);
    });
    q(".aid-canvas").appendChild(v);
    this.video = v;
    if (!play) return;
    if (reduce) {
      d.steps.forEach(function (s) { self.step(s, d, true); });
      this.finish(d, false);
      return;
    }
    var i = 0, steps = d.steps;
    // segments [real start, real end, speed] of the recording, played one after the other
    function real() {
      var vt = v.currentTime, seg = d.segments;
      for (var k = 0; k < seg.length; k++) {
        var len = (seg[k][1] - seg[k][0]) / seg[k][2];
        if (vt <= len || k === seg.length - 1) return seg[k][0] + Math.min(vt, len) * seg[k][2];
        vt -= len;
      }
      return 0;
    }
    function tick() {
      var t = real();
      while (i < steps.length && steps[i].t <= t + 1e-6) self.step(steps[i++], d);
      q(".aid-clock").textContent = fmtClock(Math.max(0, t - d.t0) / 60);
      if (!v.ended) self.raf = requestAnimationFrame(tick);
    }
    // the prompt first, then the recording starts
    while (i < steps.length && steps[i].who === "user") self.step(steps[i++], d);
    this.at(2.6, function () {
      var pr = v.play();
      if (pr && pr.catch) pr.catch(function () {});
      self.raf = requestAnimationFrame(tick);
    });
    v.addEventListener("ended", function () {
      while (i < steps.length) self.step(steps[i++], d);
      self.at(1.2, function () { self.finish(d, false); });
    });
  };
  Player.prototype.setStatus = function (runs, score, d) {
    var q = this.q;
    q(".aid-runs").textContent = runs ? runs + (runs === 1 ? " " + d.runWord : " " + d.runWord + "s") : "";
    q(".aid-meter").style.visibility = score == null ? "hidden" : "visible";
    q(".aid-meter i").style.width = (score == null ? 0 : score) + "%";
    q(".aid-score").textContent = score == null ? "" : (d.scoreLabel || "") + " " + score + " %";
  };
  Player.prototype.step = function (s, d, instant) {
    var log = this.q(".aid-log"), self = this;
    if (s.who || s.tool) {
      var line;
      if (s.tool) {
        line = el("div", "aid-tool", "<code>" + s.tool + "</code>" + (s.text ? "<span>" + s.text + "</span>" : ""));
      } else {
        line = el("div", "aid-msg " + s.who, s.who === "user" ? "" : s.text);
        if (s.who === "user" && instant) {
          line.textContent = s.text;
        } else if (s.who === "user") {
          // typed prompt
          var txt = s.text, i = 0;
          var caret = el("span", "caret");
          line.appendChild(document.createTextNode(""));
          line.appendChild(caret);
          var iv = setInterval(function () {
            i += 2;
            line.firstChild.nodeValue = txt.slice(0, i);
            if (i >= txt.length) { clearInterval(iv); caret.remove(); }
          }, 22);
          self.timers.push(iv);
        }
      }
      log.appendChild(line);
      requestAnimationFrame(function () { line.classList.add("in"); });
      while (log.children.length > 9) log.removeChild(log.firstChild);
    }
    if (this.view && !d.video) {
      (s.show || []).forEach(function (id) { self.view.show(id); });
      (s.hide || []).forEach(function (id) { self.view.hide(id); });
    }
    if (s.note != null) this.q(".aid-note").textContent = s.note;
    if (s.runs != null || s.score !== undefined) this.setStatus(s.runs, s.score, d);
  };
  Player.prototype.finish = function (d, instant) {
    var self = this, q = this.q;
    if (instant) {                      // reduced motion: the end state with the whole log at once
      d.steps.forEach(function (s) { self.step(s, d, true); });
    }
    q(".aid-clock").textContent = d.total;
    var c = d.card, card = q(".aid-card");
    card.innerHTML = "";
    var head = el("div", "aid-card-head", "<p>" + c.kicker + "</p><h3>" + c.title + "</h3>");
    var stats = el("div", "aid-stats");
    c.stats.forEach(function (s) { stats.appendChild(el("div", "", "<b>" + s[0] + "</b><span>" + s[1] + "</span>")); });
    card.appendChild(head);
    card.appendChild(stats);
    if (c.plot) card.appendChild(plot(c.plot));
    if (c.foot) card.appendChild(el("p", "aid-foot", c.foot));
    card.hidden = false;
    this.win.classList.add("done");
  };

  function init() {
    var host = document.getElementById("ai-demos");
    if (!host || !window.HZ_AI_DEMOS) return;
    var p = new Player(host, window.HZ_AI_DEMOS);
    p.select(0, false);
    var started = false;
    if ("IntersectionObserver" in window) {
      new IntersectionObserver(function (es, o) {
        if (es[0].isIntersecting && !started) { started = true; o.disconnect(); p.select(p.cur, true); }
      }, { threshold: 0.35 }).observe(host);
    } else {
      p.select(0, true);
    }
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
})();
