// The knowledge brain: topics as big glowing nodes on a ring, every save as a small "cell" around them.
// Selecting a topic zooms the camera onto it and fans its saves out on rings with readable labels.
// d3 7 (force + zoom) on SVG. All text from the server goes in via d3 .text() / textContent, never HTML.
(function () {
  const TOPIC_COLORS = ["#6c8cff", "#ff9f5a", "#e86bb7", "#3cc6a8", "#b98cff", "#f2c94c"];
  const PLATFORM = {
    "instagram-reel": ["#ff5f7e", "Reel"], "instagram-post": ["#ff9f5a", "Instagram post"], linkedin: ["#4f8df7", "LinkedIn"],
    web: ["#3cc6a8", "Web page"], "github-repo": ["#9aa1ad", "GitHub"], "github-profile": ["#9aa1ad", "GitHub"], note: ["#f2c94c", "Your note"],
  };
  const reduceMotion = matchMedia("(prefers-reduced-motion: reduce)").matches;
  const platformOf = (s) => PLATFORM[s.platform] || PLATFORM.web;
  const short = (t, n) => (t.length > n ? `${t.slice(0, n - 1).trimEnd()}…` : t);

  function nameLines(name, mobile) {
    if (mobile) return [name.split(/[ ,&]+/)[0]];
    const parts = name.split(/\s*(?:,|&)\s*/);          // "Business, Startups & Strategy" -> 2 lines
    if (parts.length < 2) return [name];
    const half = Math.ceil(parts.length / 2);
    return [parts.slice(0, half).join(", "), `& ${parts.slice(half).join(", ")}`];
  }

  function render(container, data, { onSelect, onHover, onFocusChange, onLearn } = {}) {
    if (!window.d3) throw new Error("d3 unavailable");
    const d3 = window.d3;
    container.replaceChildren();
    const width = container.clientWidth || 700, mobile = width < 600, height = mobile ? 380 : 460;
    const topics = data.topics.map((t, i) => {
      const a = (i / data.topics.length) * 2 * Math.PI - Math.PI / 2;
      const homeX = width / 2 + Math.cos(a) * width * (mobile ? 0.3 : 0.36);
      const homeY = height / 2 + Math.sin(a) * height * (mobile ? 0.32 : 0.33);
      return { ...t, r: (mobile ? 20 : 26) + Math.sqrt(t.saves) * (mobile ? 3 : 3.6), homeX, homeY, x: homeX, y: homeY, fx: homeX, fy: homeY };
    });
    const saves = data.saves.map((s) => ({ ...s, r: 4 }));
    const byId = new Map([...topics, ...saves].map((n) => [n.id, n]));
    const links = data.links.filter((l) => byId.has(l.source) && byId.has(l.target)).map((l) => ({ ...l }));
    const color = (t) => TOPIC_COLORS[t.color % TOPIC_COLORS.length];
    const endId = (e) => (typeof e === "object" ? e.id : e);

    const svg = d3.select(container).append("svg").attr("class", "brain-svg").attr("viewBox", `0 0 ${width} ${height}`)
      .attr("role", "group").attr("aria-label", "Your knowledge brain: topics from your saves");
    const defs = svg.append("defs");
    topics.forEach((t) => {
      const g = defs.append("radialGradient").attr("id", `tg-${t.id}`);
      g.append("stop").attr("offset", "0%").attr("stop-color", "#fff").attr("stop-opacity", 0.25);
      g.append("stop").attr("offset", "50%").attr("stop-color", color(t));
      g.append("stop").attr("offset", "100%").attr("stop-color", color(t)).attr("stop-opacity", 0.9);
    });
    const glow = defs.append("filter").attr("id", "brain-glow").attr("x", "-50%").attr("y", "-50%").attr("width", "200%").attr("height", "200%");
    glow.append("feGaussianBlur").attr("stdDeviation", 6).attr("result", "b");
    const merge = glow.append("feMerge"); merge.append("feMergeNode").attr("in", "b"); merge.append("feMergeNode").attr("in", "SourceGraphic");

    const bg = svg.append("rect").attr("class", "brain-bg").attr("width", width).attr("height", height).attr("fill", "transparent");
    const view = svg.append("g").attr("class", "viewport");
    const linkSel = view.append("g").attr("class", "links").selectAll("line").data(links).join("line")
      .attr("class", (l) => `link ${l.kind === "topic" ? "spoke" : "concept"}`);
    const saveG = view.append("g").attr("class", "saves").selectAll("g").data(saves).join("g").attr("class", "save");
    const saveSel = saveG.append("circle")
      .attr("class", (s) => `cell${s.held_out ? " held" : ""}`).attr("r", (s) => s.r)
      .attr("fill", (s) => (s.held_out ? "transparent" : platformOf(s)[0])).attr("stroke", (s) => platformOf(s)[0]);
    const saveLabel = saveG.append("text").attr("class", "save-label").text((s) => short(s.title, mobile ? 13 : 26));
    const topicSel = view.append("g").attr("class", "topics").selectAll("g").data(topics).join("g")
      .attr("class", "topic").attr("tabindex", 0).attr("role", "button")
      .attr("aria-label", (t) => `${t.name}: ${t.saves} saves. Select to zoom in and plan.`).attr("aria-pressed", "false");
    topicSel.append("circle").attr("class", "focus-ring").attr("r", (t) => t.r + 7);
    topicSel.append("circle").attr("class", "halo").attr("r", (t) => t.r + 10).attr("fill", (t) => color(t)).attr("filter", "url(#brain-glow)");
    topicSel.append("circle").attr("class", "core").attr("r", (t) => t.r).attr("fill", (t) => `url(#tg-${t.id})`);
    topicSel.append("text").attr("class", "count").attr("dy", "0.35em").text((t) => t.saves);
    const nameSel = topicSel.append("text").attr("class", "name");
    nameSel.each(function (t) {
      const lines = nameLines(t.name, mobile);
      d3.select(this).selectAll("tspan").data(lines).join("tspan").attr("x", 0)
        .attr("dy", (_, i) => (i === 0 ? t.r + 16 : 14)).text((l) => l);
    });

    // ---- the floating "back" button and the tooltip (plain DOM, text via textContent)
    const back = document.createElement("button");
    back.className = "brain-back chip"; back.type = "button"; back.textContent = "← Whole brain"; back.hidden = true;
    const tip = document.createElement("div");
    tip.className = "brain-tip"; tip.hidden = true;
    container.append(back, tip);
    let pinned = null, returnFocus = null;
    const line = (cls, text) => { const d = document.createElement("div"); d.className = cls; d.textContent = text; return d; };
    function actions(s) {   // only on a pinned card: the hover card disappears before you could click it
      const row = document.createElement("div");
      row.className = "tip-actions";
      if (s.learnable && s.available && !s.held_out && onLearn) {
        const learn = document.createElement("button");
        learn.type = "button"; learn.className = "chip primary-chip"; learn.textContent = "Learn it";
        learn.addEventListener("click", () => { closeTip(); onLearn(s.id); });
        row.append(learn);
      }
      if (!s.available) row.append(line("n", "No longer online: removed or made private."));
      else if (/^https?:\/\//i.test(s.url || "")) {   // never a javascript: or data: link
        const open = document.createElement("a");
        open.className = "chip"; open.href = s.url; open.target = "_blank"; open.rel = "noopener noreferrer";
        open.textContent = "Open original ↗"; open.setAttribute("aria-label", `Open the original ${platformOf(s)[1]} in a new tab`);
        row.append(open);
      }
      return row.childNodes.length ? [row] : [];
    }
    function showTip(s, at, withActions = false) {
      tip.replaceChildren(line("t", s.title),
        line("m", `${platformOf(s)[1]}${s.minutes ? ` · ${s.minutes} min` : ""}${s.held_out ? " · kept for your blind test" : ""}`),
        ...(s.note ? [line("n", `Your note: ${s.note}`)] : []), ...(withActions ? actions(s) : []));
      tip.classList.toggle("pinned", withActions);
      if (withActions) { tip.setAttribute("role", "dialog"); tip.setAttribute("aria-label", s.title); }
      else { tip.removeAttribute("role"); tip.removeAttribute("aria-label"); }
      tip.hidden = false;
      const box = container.getBoundingClientRect();
      const x = at.clientX - box.left, y = at.clientY - box.top, tw = tip.offsetWidth, th = tip.offsetHeight;
      tip.style.left = `${Math.max(6, Math.min(x + 12, box.width - tw - 6))}px`;
      tip.style.top = `${y + 12 + th > box.height ? Math.max(6, y - th - 12) : y + 12}px`;   // flip above near the bottom
    }
    const hideTip = () => { if (!pinned) tip.hidden = true; };
    function pin(s, at, { keyboard = false, opener = null } = {}) {
      pinned = s; returnFocus = opener;
      showTip(s, at, true);
      if (keyboard) tip.querySelector("button, a")?.focus();
    }
    function closeTip() {
      const back = returnFocus;
      pinned = null; returnFocus = null; tip.hidden = true;
      return back;
    }

    // ---- camera
    let k = 1;
    const zoom = d3.zoom().scaleExtent([0.6, 4]).on("zoom", (ev) => {
      view.attr("transform", ev.transform);
      k = ev.transform.k;
      saveLabel.style("font-size", `${(mobile ? 10.5 : 12) / k}px`);
      nameSel.style("font-size", `${12.5 / Math.max(1, k * 0.85)}px`);
      if (pinned) tip.hidden = true, pinned = null;
    });
    svg.call(zoom).on("dblclick.zoom", null);
    const dur = reduceMotion ? 0 : 750;
    function camera(transform) { svg.transition().duration(dur).ease(d3.easeCubicInOut).call(zoom.transform, transform); }

    // ---- focus: the selected topic's saves fan out on rings around it
    let focused = null, selectedId = null;
    // Mind-map layout: the topic in the middle, its saves in two gently curved columns either side, labels
    // pointing outward. Spacing is set in screen pixels, so labels never overlap at the chosen zoom.
    const FOCUS_SCALE = mobile ? 1.0 : 1.7, ROW_PX = mobile ? 21 : 23, GAP_PX = mobile ? 18 : 40;
    function placeRings(t) {
      const mine = saves.filter((s) => s.topics.includes(t.id));
      const sides = [mine.filter((_, i) => i % 2 === 0), mine.filter((_, i) => i % 2 === 1)];
      const sc = FOCUS_SCALE, row = ROW_PX / sc;
      sides.forEach((col, side) => {
        const dir = side === 0 ? 1 : -1, n = col.length;
        col.forEach((s, j) => {
          const u = n > 1 ? j / (n - 1) - 0.5 : 0;             // -0.5 .. 0.5 down the column
          s.tx = t.x + dir * (t.r + GAP_PX / sc + Math.abs(u) * u * 0 + (0.25 - u * u) * 28 / sc);   // a soft outward arc
          s.ty = t.y + u * (n - 1) * row;
          s.side = dir;
        });
      });
      return { mine, scale: sc };
    }
    function focusOn(t) {
      closeTip();
      focused = t;
      const { mine, scale } = placeRings(t);
      const ids = new Set(mine.map((s) => s.id));
      svg.classed("has-focus", true);
      saveG.classed("focus", (s) => ids.has(s.id)).attr("tabindex", (s) => (ids.has(s.id) ? 0 : null))
        .attr("role", (s) => (ids.has(s.id) ? "button" : null))
        .attr("aria-label", (s) => (ids.has(s.id) ? `${s.title} (${platformOf(s)[1]})` : null));
      saveLabel.attr("text-anchor", (s) => (s.side >= 0 ? "start" : "end"))
        .attr("dx", (s) => (s.side >= 0 ? 10 : -10)).attr("dy", "0.35em");
      sim.force("x").x((n) => (n.kind === "topic" ? n.homeX : ids.has(n.id) ? n.tx : width / 2)).strength((n) => (n.kind === "topic" ? 1 : ids.has(n.id) ? 0.9 : 0.02));
      sim.force("y").y((n) => (n.kind === "topic" ? n.homeY : ids.has(n.id) ? n.ty : height / 2)).strength((n) => (n.kind === "topic" ? 1 : ids.has(n.id) ? 0.9 : 0.03));
      sim.force("link").strength((l) => (ids.has(endId(l.source)) || ids.has(endId(l.target)) ? 0 : l.kind === "topic" ? 0.25 : 0.03));
      refreshCollide();
      sim.alpha(0.8).restart();
      camera(d3.zoomIdentity.translate(width / 2, height / 2).scale(scale).translate(-t.x, -t.y));
      back.hidden = false;
      onFocusChange?.(t, mine);
    }
    function unfocus() {
      closeTip();
      if (!focused) return;
      focused = null;
      svg.classed("has-focus", false);
      saveG.classed("focus", false).attr("tabindex", null).attr("role", null).attr("aria-label", null);
      sim.force("x").x((n) => (n.kind === "topic" ? n.homeX : width / 2)).strength((n) => (n.kind === "topic" ? 1 : 0.02));
      sim.force("y").y((n) => (n.kind === "topic" ? n.homeY : height / 2)).strength((n) => (n.kind === "topic" ? 1 : 0.03));
      sim.force("link").strength((l) => (l.kind === "topic" ? 0.25 : 0.03));
      refreshCollide();
      sim.alpha(0.6).restart();
      camera(d3.zoomIdentity);
      back.hidden = true; pinned = null; tip.hidden = true;
      onFocusChange?.(null, []);
    }

    function select(name, { user = false, zoomIn = true } = {}) {
      const t = topics.find((x) => x.name === name) || null;
      selectedId = t?.id || null;
      svg.classed("has-selection", !!t);
      topicSel.classed("on", (x) => x.id === selectedId).attr("aria-pressed", (x) => String(x.id === selectedId));
      saveSel.classed("bloom", (s) => s.topics.includes(selectedId)).attr("r", (s) => (s.topics.includes(selectedId) ? (mobile ? 6 : 7) : 4));
      linkSel.classed("lit", (l) => {
        const a = byId.get(endId(l.source)), b = byId.get(endId(l.target));
        return l.kind === "topic" ? b.id === selectedId : (a.topics || []).includes(selectedId) && (b.topics || []).includes(selectedId);
      });
      if (t && zoomIn) focusOn(t); else if (!t) unfocus();
      if (t && user) onSelect?.(t.name);
    }

    // ---- interaction
    topicSel.on("click", (ev, t) => { ev.stopPropagation(); select(t.name, { user: true }); })
      .on("keydown", (ev, t) => { if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); select(t.name, { user: true }); } })
      .on("mouseenter", (ev, t) => onHover?.(t)).on("mouseleave", () => onHover?.(null));   // not on focus: aria-label covers keyboard users
    saveG.on("mouseenter", (ev, s) => { d3.select(ev.currentTarget).classed("hover", true); if (!pinned) showTip(s, ev); })
      .on("mouseleave", (ev) => { d3.select(ev.currentTarget).classed("hover", false); hideTip(); })
      .on("click", (ev, s) => { ev.stopPropagation(); pin(s, ev); })
      .on("keydown", (ev, s) => {
        if (ev.key !== "Enter" && ev.key !== " ") return;
        ev.preventDefault();
        const r = ev.currentTarget.getBoundingClientRect();
        pin(s, { clientX: r.right, clientY: r.top }, { keyboard: true, opener: ev.currentTarget });
      });
    bg.on("click", () => closeTip());
    svg.on("dblclick", (ev) => { if (ev.target === bg.node()) unfocus(); });
    back.addEventListener("click", unfocus);
    container.addEventListener("keydown", (ev) => {
      if (ev.key === "Escape" && pinned) { ev.preventDefault(); ev.stopPropagation(); closeTip()?.focus(); return; }   // the card first, then the zoom
      if (ev.key === "Escape" && focused) { ev.preventDefault(); unfocus(); topicSel.filter((t) => t.id === selectedId).node()?.focus(); } });

    // ---- forces: topics pinned on the ring; saves gather around their topic
    const sim = d3.forceSimulation([...topics, ...saves])
      .force("link", d3.forceLink(links).id((n) => n.id).distance((l) => (l.kind === "topic" ? (mobile ? 44 : 58) : 40))
        .strength((l) => (l.kind === "topic" ? 0.25 : 0.03)))
      .force("charge", d3.forceManyBody().strength((n) => (n.kind === "topic" ? 0 : -14)))
      .force("x", d3.forceX((n) => (n.kind === "topic" ? n.homeX : width / 2)).strength((n) => (n.kind === "topic" ? 1 : 0.02)))
      .force("y", d3.forceY((n) => (n.kind === "topic" ? n.homeY : height / 2)).strength((n) => (n.kind === "topic" ? 1 : 0.03)))
      .force("collide", d3.forceCollide((n) => (n.kind === "topic" ? n.r + 12 : focused && n.topics.includes(focused.id) ? 9 : 6)))
      .on("tick", () => {
        for (const n of saves) {                           // keep cells inside the box (not the fanned-out ones)
          if (focused && n.topics.includes(focused.id)) continue;
          n.x = Math.max(8, Math.min(width - 8, n.x)); n.y = Math.max(8, Math.min(height - 8, n.y));
        }
        linkSel.attr("x1", (l) => l.source.x).attr("y1", (l) => l.source.y).attr("x2", (l) => l.target.x).attr("y2", (l) => l.target.y);
        saveG.attr("transform", (s) => `translate(${s.x},${s.y})`);
        topicSel.attr("transform", (t) => `translate(${t.x},${t.y})`);
      });
    const drift = reduceMotion ? 0 : 0.008;               // a gentle, endless drift: the brain is alive
    sim.alphaDecay(0.03).alphaTarget(drift);
    topicSel.call(d3.drag().clickDistance(5)
      .on("start", (ev) => { if (!ev.active) sim.alphaTarget(0.2).restart(); })
      .on("drag", (ev, t) => { t.fx = t.homeX = ev.x; t.fy = t.homeY = ev.y; })
      .on("end", (ev) => { if (!ev.active) sim.alphaTarget(drift); }));

    function refreshCollide() {                        // d3 caches radii; re-apply so focused cells get room
      sim.force("collide").radius((n) => (n.kind === "topic" ? n.r + 12 : focused && n.topics.includes(focused.id) ? 0 : 6));
    }

    let running = true;
    const pause = () => { if (running) { running = false; sim.stop(); } };
    const resume = () => { if (!running) { running = true; sim.alphaTarget(drift).restart(); } };
    document.addEventListener("visibilitychange", () => (document.hidden ? pause() : !container.closest("[hidden]") && resume()));

    return {
      select: (name, opts) => select(name, opts || { zoomIn: false }),
      unfocus, pause, resume,
      highlight: (id, on) => saveG.filter((s) => s.id === id).classed("hover", on),
      pin: (id, opener) => {   // a chip under the graph: the same card, next to that save's dot
        const node = saveG.filter((s) => s.id === id);
        if (node.empty()) return false;
        const r = node.node().getBoundingClientRect();
        pin(node.datum(), { clientX: r.right, clientY: r.top }, { keyboard: true, opener });
        return true;
      },
      setStates: (map) => {                               // mastery overlay: {save id: new|learning|review|mastered} or null
        svg.classed("mastery", !!map);
        saveG.attr("data-state", (s) => (map ? map[s.id] || "new" : null));
      },
    };
  }

  window.Brain = { render, TOPIC_COLORS, PLATFORM };
})();
