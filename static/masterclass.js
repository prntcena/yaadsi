// "Connect the dots": a deep-reasoning pass of Muse Spark Open over a whole topic (masterclass.py).
// It builds in the background; this view polls, then renders ideas, a connection ring, disagreements, gaps and an order.
(function () {
  const $ = (sel) => document.querySelector(sel);
  const NS = "http://www.w3.org/2000/svg";
  const LINES = (topic, n) => [`Reading all ${n} of your ${topic} saves…`, "Lining them up against everything else you saved…",
    "Looking for where they agree and where they don't…", "Finding what your collection is missing…", "Working out the best order to learn them…"];
  let topic = "", from = "progress", poll = null, tick = null, started = 0, result = null, restarted = false;

  function svg(tag, attrs) {
    const n = document.createElementNS(NS, tag);
    for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, v);
    return n;
  }
  const card = (id) => result.cards[id] || { title: id, platform: "web" };
  function chip(id) {
    const c = card(id);
    const [dot] = (window.Brain?.PLATFORM || {})[c.platform] || ["#9aa1ad"];
    return el("button", { class: "chip save-chip", type: "button", title: `${c.title}: open a mini lesson`, onclick: () => openMini(id) },
      el("i", { style: `background:${dot};border-color:${dot}` }), c.title.length > 38 ? `${c.title.slice(0, 37)}…` : c.title);
  }

  async function open(name, back) {
    topic = name; from = back || "progress"; result = null;
    show("masterclass");
    $("#mc-topic").textContent = name;
    $("#mc-body").hidden = true; $("#mc-error").hidden = true; restarted = false;
    try { handle(await api("/api/masterclass", { topic: name })); } catch (e) { fail(e.message); }
  }

  function handle(s) {
    if ($("#view-masterclass").hidden) return stop();
    if (s.status === "ready") { stop(); return render(s.result); }
    if (s.status === "failed") return fail(s.error);
    if (s.status === "idle") {   // the build is gone (server restart or a new save): start it again, once
      if (restarted) return fail("");
      restarted = true;
      api("/api/masterclass", { topic }).then(handle, (e) => fail(e.message));
      return;
    }
    if (!tick) {   // running: rotate Yaad's lines and the elapsed counter
      started = Date.now() - (s.elapsed || 0) * 1000;
      const lines = LINES(topic, s.focus || "");
      let i = 0;
      $("#mc-loading").hidden = false; $("#mc-line").textContent = lines[0];
      Yaad?.think(true); Yaad?.say(lines[0], 0);
      tick = setInterval(() => {
        if ($("#view-masterclass").hidden) return stop();
        const secs = Math.round((Date.now() - started) / 1000);
        $("#mc-elapsed").textContent = `${secs}s`;
        if (secs % 6 === 0) { i = (i + 1) % lines.length; $("#mc-line").textContent = lines[i]; Yaad?.say(lines[i], 0); }
      }, 1000);
    }
    clearTimeout(poll);
    poll = setTimeout(async () => {
      try { handle(await api(`/api/masterclass?topic=${encodeURIComponent(topic)}`)); } catch (e) { fail(e.message); }
    }, 3000);
  }

  function stop() {
    clearTimeout(poll); clearInterval(tick); tick = null;
    $("#mc-loading").hidden = true;
    Yaad?.think(false);
  }
  function fail(msg) {
    stop();
    $("#mc-error").replaceChildren(msg || "Something went wrong.", " ", el("button", { class: "chip", type: "button", onclick: () => open(topic, from) }, "Try again"));
    $("#mc-error").hidden = false;
    Yaad?.mood("sad", 3000);
  }

  function render(r) {
    result = r;
    $("#mc-body").hidden = false;
    const step = Object.fromEntries(r.learning_order.map((o, i) => [o.id, i + 1]));
    $("#mc-ideas").replaceChildren(...r.core_ideas.map((c, i) => el("article", { class: "card mc-idea" },
      el("div", { class: "mc-n" }, String(i + 1)), el("h3", {}, c.idea),
      el("p", {}, el("b", {}, "Why it matters: "), c.why), c.trade_off ? el("p", { class: "muted" }, el("b", {}, "Trade-off: "), c.trade_off) : "",
      el("div", { class: "chips small" }, ...c.saves.map(chip)))));
    drawRing(r, step);
    $("#mc-links").replaceChildren(...r.connections.map((c, i) => {
      const li = el("li", { "data-link": i, tabindex: "0" }, el("span", { class: "mc-pair" }, `${step[c.a] ? `#${step[c.a]} ` : ""}${card(c.a).title}`, " ↔ ",
        `${step[c.b] ? `#${step[c.b]} ` : ""}${card(c.b).title}`), c.cross_topic ? el("span", { class: "pill next" }, "other topic") : "", el("div", { class: "muted small" }, c.reason));
      const on = (v) => () => highlight(i, v);
      li.addEventListener("mouseenter", on(true)); li.addEventListener("focus", on(true));
      li.addEventListener("mouseleave", on(false)); li.addEventListener("blur", on(false));
      return li;
    }));
    $("#mc-link-hint").textContent = r.connections.length ? "Numbers are the learning order below. Hover a line or a link to see why they connect." : "No strong links found between these saves.";
    $("#mc-disagree-box").hidden = !r.disagreements.length;
    $("#mc-disagree").replaceChildren(...r.disagreements.map((d) => el("li", {}, el("p", {}, d.point), el("div", { class: "chips small" }, ...d.saves.map(chip)))));
    $("#mc-gaps-box").hidden = !r.gaps.length;
    $("#mc-gaps").replaceChildren(...r.gaps.map((g) => el("div", { class: "mc-gap" }, el("b", {}, g.missing), el("p", { class: "muted small" }, g.why_it_matters))));
    $("#mc-order").replaceChildren(...r.learning_order.map((o) => el("li", {}, chip(o.id), card(o.id).minutes ? timeChip(card(o.id).minutes) : "",
      el("span", { class: "muted small" }, o.unplaced ? " (Yaad didn't place this one)" : o.why_here ? ` ${o.why_here}` : ""))));
    $("#mc-plan-status").textContent = "";
    $("#mc-foot").textContent = `Read all ${r.saves_read} of your saves (${r.focus} in this topic) · thought for ${r.seconds}s · Muse Spark Open, deep reasoning · ${r.built_at.replace("T", " ")}`;
    Yaad?.mood("happy", 2000);
    Yaad?.say(`${r.core_ideas.length} big ideas and ${r.connections.length} links hiding in your saves!`, 5000);
  }

  function drawRing(r, step) {
    const ids = r.learning_order.map((o) => o.id);
    const box = $("#mc-ring");
    if (ids.length < 2 || !r.connections.length) { box.replaceChildren(); return; }
    const S = 360, C = S / 2, R = 140, pos = {};
    ids.forEach((id, i) => { const a = (2 * Math.PI * i) / ids.length - Math.PI / 2; pos[id] = [C + R * Math.cos(a), C + R * Math.sin(a)]; });
    const root = svg("svg", { viewBox: `0 0 ${S} ${S}`, role: "img", "aria-label": `${r.connections.length} connections between ${ids.length} saves` });
    r.connections.forEach((c, i) => {
      if (!pos[c.a] || !pos[c.b]) return;   // cross-topic links are listed below the ring
      const [x1, y1] = pos[c.a], [x2, y2] = pos[c.b];
      const path = svg("path", { d: `M${x1},${y1} Q${C},${C} ${x2},${y2}`, class: "mc-edge", "data-link": i });
      const t = svg("title", {}); t.textContent = c.reason; path.append(t);
      path.addEventListener("mouseenter", () => highlight(i, true)); path.addEventListener("mouseleave", () => highlight(i, false));
      root.append(path);
    });
    ids.forEach((id) => {
      const c = card(id), [x, y] = pos[id];
      const [dot] = (window.Brain?.PLATFORM || {})[c.platform] || ["#9aa1ad"];
      const g = svg("g", { class: "mc-node", "data-id": id });
      const t = svg("title", {}); t.textContent = c.title;
      const num = svg("text", { x, y: y + 4, "text-anchor": "middle" }); num.textContent = String(step[id]);
      g.append(t, svg("circle", { cx: x, cy: y, r: 13, fill: dot }), num);
      root.append(g);
    });
    box.replaceChildren(root);
  }

  function highlight(i, on) {
    const c = result?.connections[i];
    document.querySelectorAll(`#view-masterclass [data-link="${i}"]`).forEach((n) => n.classList.toggle("hot", on));
    if (c) document.querySelectorAll("#mc-ring .mc-node").forEach((n) => n.classList.toggle("hot", on && (n.dataset.id === c.a || n.dataset.id === c.b)));
  }

  $("#mc-back").addEventListener("click", () => show(from));
  $("#mc-plan").addEventListener("click", async () => {
    if (!result) return;
    const d = new Date(); d.setDate(d.getDate() + 30);
    $("#mc-plan").disabled = true; $("#mc-plan-status").textContent = "Building your plan…";
    try {
      const formats = state.formats?.length ? state.formats : ["diagram", "quiz"];
      const { plan, level } = await api("/api/start", { topic, goal: "", deadline: d.toISOString().slice(0, 10),
        minutes_per_day: +$("#minutes").value || 20, depth: state.level || +$("#depth").value || 2, formats,
        order: result.learning_order.map((o) => o.id) });
      state.topic = topic; state.level = level; state.formats = formats; state.doneDays = [];
      renderPlan(plan, `${topic}, in Yaad's order`);
      Yaad?.say(`${plan.days_needed} days, in the order that builds up best. Lessons are getting ready! ⚡`, 5000);
    } catch (e) { $("#mc-plan-status").textContent = e.message; }
    finally { $("#mc-plan").disabled = false; }
  });
  window.Masterclass = { open };
})();
