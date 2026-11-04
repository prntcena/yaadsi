// Yaadsi Tutor front end. All model text goes through textContent; Mermaid runs in strict mode.
const $ = (sel) => document.querySelector(sel);
const DEPTH = ["", "Like I'm 10", "Beginner", "Summary notes", "Detailed", "Deep article"];
const ALL_FORMATS = ["diagram", "quiz", "figure", "picture", "animation", "audio"];
const state = { topic: "", lesson: null, day: 1, level: 2, formats: [...ALL_FORMATS], openSeq: 0, doneDays: [], readyDays: [], lastView: null, plan: null, quiz: { right: 0, answered: 0 } };
const Yaad = window.Yaad;                      // the buddy (yaad.js); optional
// Yaad's mouth moves whenever the app is speaking (lesson Listen, narration, answers)
setInterval(() => Yaad?.talk(!!window.speechSynthesis && speechSynthesis.speaking), 250);

if (window.mermaid) mermaid.initialize({ startOnLoad: false, securityLevel: "strict", theme: matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "neutral" });

async function api(path, body, signal) {   // X-Yaadsi forces a CORS preflight, so other sites cannot POST here
  const res = await fetch(path, body ? { method: "POST", headers: { "Content-Type": "application/json", "X-Yaadsi": "1" }, body: JSON.stringify(body), signal } : { signal });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(typeof data.detail === "string" ? data.detail : "Something went wrong.");
  return data;
}

// Streamed variant: the server sends NDJSON lines {type: thought|done|error}. Live thoughts go to onThought;
// quiet spells get gentle static lines. If the stream breaks before anything arrives, fall back to the plain call.
const QUIET_LINES = ["Still thinking it through…", "Checking it against your saves…", "Almost there…"];
async function streamApi(path, body, onThought, signal) {
  let got = false, last = Date.now(), quiet = 0;
  const idle = setInterval(() => { if (Date.now() - last > 4000) { last = Date.now(); onThought(QUIET_LINES[quiet++ % QUIET_LINES.length]); } }, 1000);
  try {
    const res = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json", "X-Yaadsi": "1" }, body: JSON.stringify(body), signal });
    if (!res.ok || !res.body) {
      const data = await res.json().catch(() => ({}));
      throw Object.assign(new Error(typeof data.detail === "string" ? data.detail : "Something went wrong."), { fromServer: res.status !== 404 });
    }
    const reader = res.body.getReader(), dec = new TextDecoder();
    let buf = "";
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      let nl;
      while ((nl = buf.indexOf("\n")) >= 0) {
        const line = buf.slice(0, nl).trim(); buf = buf.slice(nl + 1);
        if (!line) continue;
        got = true;
        const msg = JSON.parse(line);
        if (msg.type === "thought") { last = Date.now(); onThought(String(msg.text).slice(0, 160)); }
        else if (msg.type === "done") return msg.data;
        else if (msg.type === "error") throw Object.assign(new Error(msg.detail || "Something went wrong."), { fromServer: true });
      }
    }
    throw new Error("The connection dropped.");
  } catch (e) {
    if (e.name === "AbortError" || e.fromServer || got) throw e;
    return api(path.replace(/\/stream$/, ""), body, signal);   // stream unavailable: the plain endpoint
  } finally { clearInterval(idle); }
}

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "href" && !/^(https?:\/\/|\/(?!\/)|#)/i.test(String(v))) continue;   // never javascript:/data: links
    if (k === "class") node.className = v; else if (k.startsWith("on")) node.addEventListener(k.slice(2), v); else node.setAttribute(k, v);
  }
  for (const c of children) node.append(c instanceof Node ? c : document.createTextNode(String(c)));
  return node;
}

// Material Symbols ligature icons. If the icon font never loads, body.no-icons hides the raw words.
const icon = (name, cls = "") => el("span", { class: `ms ${cls}`.trim(), "aria-hidden": "true" }, name);
function setBtn(btn, name, label) { btn.replaceChildren(icon(name), label); }
// How long a save takes: quick ones vs deep reads (the backlog is a mix of both)
function timeChip(m) {
  m = Math.max(1, Math.round(+m || 0));
  const kind = m <= 5 ? "quick" : m >= 20 ? "deep" : "";
  return el("span", { class: `time ${kind}`, title: kind === "quick" ? "A quick one" : kind === "deep" ? "A deep read: needs your focus" : "" },
    icon(kind === "quick" ? "bolt" : kind === "deep" ? "menu_book" : "schedule"), kind === "deep" ? `${m} min deep read` : `${m} min`);
}
const keptBadge = (s) => (s?.kept ? el("span", { class: "kept", title: "Yaad saved the notes, even if the post disappears", "aria-label": "Notes saved" }, icon("inventory_2")) : "");
const DEMO = new URLSearchParams(location.search).get("demo") === "1";   // internals (blind test, spend, judge) only here
document.body.classList.toggle("demo", DEMO);
document.fonts?.ready.then(() => setTimeout(() => {
  const ok = [...document.fonts].some((f) => f.family.replace(/["']/g, "") === "Material Symbols Rounded" && f.status === "loaded");
  document.body.classList.toggle("no-icons", !ok);
}, 3000));

function show(view) {
  if (view !== "lesson") { stopPlayer(); stopListen(); }   // no lesson audio over other screens
  for (const v of ["setup", "plan", "lesson", "eval", "progress", "masterclass"]) $(`#view-${v}`).hidden = v !== view;
  if (view === "progress") window.Progress?.load().catch(() => {});
  document.querySelectorAll("[data-go]").forEach((b) => {   // show where you are
    const on = b.dataset.go === view; b.classList.toggle("active", on);
    if (on) b.setAttribute("aria-current", "page"); else b.removeAttribute("aria-current");
  });
  if (view === "eval") loadEval().catch(() => {});
  if (view === "setup") { ensureBrain(); brain?.resume(); loadToday(); } else brain?.pause();   // no ticking while hidden
  if (view === "plan") { loadLearner(); if (state.plan) drawPlan(state.plan); pollPrewarm(); } else clearTimeout(prewarmTimer);
  if ((view === "plan" || view === "lesson") && state.plan) setTopicColor(state.plan.topic);
  [...document.body.classList].filter((c) => c.startsWith("view-")).forEach((c) => document.body.classList.remove(c));
  document.body.classList.add(`view-${view}`);       // Ask and Yaad sit above Learn's sticky bar (style.css)
  if (window.Yaad) {
    placeYaad();
    Yaad.rest(view === "setup" && heroInView ? YAAD_LINES.setup : null);
    const line = YAAD_LINES[view];
    if (line && view !== "setup" && view !== state.lastView) Yaad.say(typeof line === "function" ? line() : line, 4200);
  }
  state.lastView = view;
  window.scrollTo(0, 0);
}

// Yaad is always on screen: in the Learn hero while it is visible, otherwise docked in the corner.
let heroInView = true;
function placeYaad() {
  const onLearn = document.body.classList.contains("view-setup"), inHero = onLearn && heroInView;
  const moved = window.Yaad && window.Yaad.el.parentElement !== (inHero ? $("#yaad-hero") : $("#yaad-dock"));
  window.Yaad?.mount(inHero ? $("#yaad-hero") : $("#yaad-dock"));
  document.body.classList.toggle("yaad-docked", !inHero);
  if (onLearn && moved) window.Yaad?.rest(inHero ? YAAD_LINES.setup : null);
}
if ("IntersectionObserver" in window) {
  new IntersectionObserver(([e]) => { heroInView = e.intersectionRatio >= 0.35; placeYaad(); }, { threshold: [0, 0.35, 0.7, 1] })
    .observe($("#yaad-hero"));
}

const YAAD_LINES = {
  setup: "Hi, I'm Yaad! Every day I pick a short lesson from your saves.",
  plan: () => (state.doneDays.length ? `${state.doneDays.length} day(s) done. Keep going!` : "Here's your plan. Start with Day 1 whenever you like."),
  eval: "Pick the version that teaches you better. I won't tell you which is which!",
};
document.querySelectorAll("[data-go]").forEach((b) => b.addEventListener("click", () => show(b.dataset.go)));

// ---------- setup ----------
let topicsData = [];
async function loadTopics({ initial = true } = {}) {
  const { topics, today } = await api("/api/topics");
  topicsData = topics;
  const box = $("#topics");
  box.replaceChildren(...topics.map((t) => {
    const b = el("button", { class: "chip", onclick: () => { pickTopic(t.name); if (!brain) openCustomise(); } }, t.name, el("small", {}, `${t.saves}`));   // no graph: the chips are the planner
    b.dataset.topic = t.name;
    return b;
  }));
  $("#deadline").min = today;
  if (initial) setDeadline(14);
  else if (state.topic) document.querySelectorAll("#topics .chip").forEach((c) => c.classList.toggle("on", c.dataset.topic === state.topic));
}

let brain = null, brainLoading = null;
function ensureBrain() {                            // laid out the first time Setup is visible (needs a real width)
  if (!brainLoading && !$("#view-setup").hidden) brainLoading = loadBrain();
}
async function loadBrain() {
  try {
    const data = await api("/api/graph");
    brain = Brain.render($("#brain"), data, {
      onSelect: (name) => pickTopic(name),   // zoom in only; "Plan this topic" opens the planner
      onLearn: (id) => openMini(id),
      onHover: (t) => {
        if (!window.Yaad || !t) return;
        Yaad.mood("curious", 1500);
        Yaad.say(`You saved ${t.saves} things about ${t.name}!`, 2500);
      },
      onFocusChange: (t, mine) => {
        renderTopicSaves(t, mine);
        if (t) Yaad?.say(`Here are your ${mine.length} saves about ${t.name}. Hover or tap one to peek!`, 4500);
      },
    });
    if (state.topic) brain.select(state.topic);
    window.__brainSelect = (name) => brain?.select(name, { zoomIn: true });
  } catch {
    brain = null;                                     // no d3 (offline/blocked): the chips are the picker
    $("#brain").hidden = true; $("#topics").hidden = false; $("#brain-toggle").hidden = true;
    document.querySelector(".brain-legend").hidden = true;
    $("#brain-hint").textContent = "Pick a topic from your saves.";
  }
}

function renderTopicSaves(t, mine) {
  const box = $("#topic-saves");
  box.hidden = !t;
  if (!t) { box.replaceChildren(); return; }
  box.replaceChildren(el("div", { class: "topic-saves-head" }, el("span", { class: "muted small" }, `Saves in ${t.name}`),
    el("span", { class: "topic-saves-actions" },
      el("button", { class: "chip", type: "button", onclick: () => { pickTopic(t.name); openCustomise(); } }, icon("tune"), "Plan this topic"),
      el("button", { class: "chip dots-btn", type: "button", onclick: () => window.Masterclass?.open(t.name, "setup") }, icon("hub"), "Connect the dots"))),
    el("div", { class: "chips small" },
    ...mine.map((s) => {
      const [dot, kind] = Brain.PLATFORM[s.platform] || Brain.PLATFORM.web;
      const chip = el("button", { class: "chip save-chip", type: "button", title: `${s.title} (${kind})` },
        el("i", { style: `background:${s.held_out ? "transparent" : dot};border-color:${dot}` }),
        s.title.length > 34 ? `${s.title.slice(0, 33)}…` : s.title, s.minutes ? el("span", { class: "chip-time" }, `${s.minutes}m`) : "");
      const on = () => brain?.highlight(s.id, true), off = () => brain?.highlight(s.id, false);
      chip.addEventListener("mouseenter", on); chip.addEventListener("focus", on);
      chip.addEventListener("mouseleave", off); chip.addEventListener("blur", off);
      chip.addEventListener("click", () => { if (!brain?.pin(s.id, chip)) openMini(s.id); });   // Learn it / Open original
      return chip;
    })));
}

$("#mastery-toggle").addEventListener("change", async (ev) => {
  if (!brain) return;
  if (!ev.target.checked) { brain.setStates(null); return; }
  try {
    const p = await api("/api/progress");
    brain.setStates(Object.fromEntries(p.saves.map((s) => [s.id, s.state])));
    Yaad?.say(`🏆 ${p.stats.mastered} mastered · 🌱 ${p.stats.learning} learning · 🔁 ${p.stats.review} to review`, 4500);
  } catch { ev.target.checked = false; }
});

$("#brain-toggle").addEventListener("click", () => {
  const list = $("#topics").hidden;                   // switching to list view?
  $("#topics").hidden = !list; $("#brain").hidden = list;
  const label = list ? "Brain view" : "List view";
  $("#brain-toggle").replaceChildren(icon(list ? "bubble_chart" : "format_list_bulleted"), el("span", { class: "sr-only" }, label));
  $("#brain-toggle").title = label;
  $("#brain-toggle").setAttribute("aria-pressed", String(list));
});

function pickTopic(name) {
  state.topic = name;
  document.querySelectorAll("#topics .chip").forEach((c) => c.classList.toggle("on", c.dataset.topic === name));
  const t = topicsData.find((x) => x.name === name);
  $("#topic-concepts").textContent = t ? "Includes: " + t.concepts.join(" · ") : "";
  brain?.select(name);
  setTopicColor(name, $("#view-setup"));                // the plan keeps its own colour
  refreshPreview();
}

function setTopicColor(name, target = document.documentElement) {
  const i = topicsData.findIndex((x) => x.name === name);
  if (i >= 0 && window.Brain) target.style.setProperty("--topic", Brain.TOPIC_COLORS[i % Brain.TOPIC_COLORS.length]);
}

function setDeadline(days) {
  const d = new Date(); d.setDate(d.getDate() + days);
  $("#deadline").value = d.toISOString().slice(0, 10);
  refreshPreview();
}
document.querySelectorAll("[data-days]").forEach((b) => b.addEventListener("click", () => setDeadline(+b.dataset.days)));

function syncLabels() {
  $("#minutes-out").textContent = `${$("#minutes").value} min`;
  $("#depth-out").textContent = DEPTH[+$("#depth").value];
  document.querySelectorAll("#depth-seg [data-depth]").forEach((b) => {
    const on = +b.dataset.depth === +$("#depth").value;
    b.classList.toggle("on", on); b.setAttribute("aria-checked", String(on)); b.tabIndex = on ? 0 : -1;
  });
}
document.querySelectorAll("#depth-seg [data-depth]").forEach((b) => b.addEventListener("click", () => {
  $("#depth").value = b.dataset.depth;
  $("#depth").dispatchEvent(new Event("input"));
}));
$("#depth-seg").addEventListener("keydown", (ev) => {   // radio group: arrows move the choice
  const step = { ArrowRight: 1, ArrowUp: 1, ArrowLeft: -1, ArrowDown: -1 }[ev.key];
  if (!step) return;
  ev.preventDefault();
  $("#depth").value = Math.min(5, Math.max(1, +$("#depth").value + step));
  $("#depth").dispatchEvent(new Event("input"));
  $(`#depth-seg [data-depth="${$("#depth").value}"]`).focus();
});
for (const id of ["#minutes", "#depth", "#deadline", "#goal"]) $(id).addEventListener("input", () => { syncLabels(); refreshPreview(); });

let previewTimer;
function refreshPreview() {
  clearTimeout(previewTimer);
  previewTimer = setTimeout(async () => {
    if (!state.topic) return;
    try {
      const p = await api("/api/plan", { topic: state.topic, goal: $("#goal").value, deadline: $("#deadline").value, minutes_per_day: +$("#minutes").value });
      const box = $("#preview");
      box.replaceChildren(el("div", { class: "sum" },
        el("span", { title: p.held_out ? `+${p.held_out} kept for your blind test` : "Saves in this plan" }, icon("bookmark"), `${p.saves} saves`),
        el("span", { title: `${Math.round(p.total_minutes / 60 * 10) / 10} hours in total` }, icon("calendar_today"), `${p.days_needed} days`),
        el("span", { title: "Each day" }, icon("schedule"), `${$("#minutes").value} min`),
        p.fits ? el("span", { class: "ok", title: `Fits before your deadline (${p.days_available} days)`, "aria-label": "Fits your deadline" }, icon("check_circle"))
               : el("span", { class: "warn", title: `Needs ${p.minutes_needed_per_day} min/day to fit` }, icon("warning"), `+${p.days_needed - p.days_available} days`)));
      $("#start").disabled = false;
    } catch (e) { $("#preview").textContent = e.message; }
  }, 150);
}

$("#start").addEventListener("click", async () => {
  const formats = [...document.querySelectorAll("#formats input:checked")].map((i) => i.value);
  try {
    const { plan, level } = await api("/api/start", { goal: $("#goal").value, topic: state.topic, deadline: $("#deadline").value,
      minutes_per_day: +$("#minutes").value, depth: +$("#depth").value, formats });
    state.level = level; state.formats = formats; state.doneDays = [];
    renderPlan(plan, $("#goal").value);
    Yaad?.mood("happy");
    Yaad?.say(`${plan.days_needed} days, ${$("#minutes").value} minutes a day. We've got this! 💪`, 5000);
  } catch (e) { $("#preview").textContent = e.message; }
});

// ---------- plan ----------
function renderPlan(plan, goal) {
  state.goal = goal;
  drawPlan(plan);
  show("plan");
}

function drawPlan(plan) {
  const goal = state.goal;
  $("#nav-plan").hidden = false;
  $("#plan-title").textContent = goal || plan.topic;
  $("#plan-sub").textContent = `${goal ? `${plan.topic} · ` : ""}${plan.saves} saves · ${plan.days_needed} days · level: ${DEPTH[state.level]}`;
  const done = new Set(state.doneDays);
  const today = plan.days.find((d) => !done.has(d.day))?.day;
  $("#days").replaceChildren(...plan.days.map((d) => {
    const status = done.has(d.day) ? ["done", "Done ✓"] : d.day === today ? ["today", "Today"] : ["next", "Upcoming"];
    const bolt = el("span", { class: "bolt ms", title: "Lesson ready: opens instantly", "aria-label": "Ready" }, "bolt");
    bolt.hidden = !state.readyDays.includes(d.day);
    return el("li", { class: status[0], "data-day": d.day },
      el("div", {}, el("b", {}, `Day ${d.day}`), el("span", { class: `pill ${status[0]}` }, status[1]), bolt,
        ` · ${d.date} · ${d.minutes} min`, el("div", { class: "titles" }, d.titles.join(" · "))),
      el("button", { class: status[0] === "done" ? "chip" : "primary", onclick: () => openLesson(d.day) }, status[0] === "done" ? "Review" : "Start"));
  }));
  const pct = plan.days.length ? Math.round((100 * done.size) / plan.days.length) : 0;
  $("#ring-fg").style.strokeDasharray = `${pct} 100`;
  $("#ring-fg").style.visibility = pct ? "visible" : "hidden";   // no stray dot at 0%
  $("#ring-text").textContent = `${pct}%`;
  $("#plan-ring").setAttribute("aria-label", `${done.size} of ${plan.days.length} days done`);
  state.plan = plan;
  setTopicColor(plan.topic);
}

// Lessons are built in the background (prewarm.py); poll only while the plan is on screen and work is pending.
let prewarmTimer = null;
async function pollPrewarm() {
  clearTimeout(prewarmTimer);
  if ($("#view-plan").hidden) return;
  let s;
  try { s = await api("/api/prewarm"); } catch { return; }
  if ($("#view-plan").hidden) return;
  $("#prewarm").hidden = !s.enabled || !s.total;
  state.readyDays = s.days_ready || [];
  document.querySelectorAll("#days li[data-day]").forEach((li) => {
    li.querySelector(".bolt").hidden = !state.readyDays.includes(+li.dataset.day);
  });
  const all = s.ready >= s.total;
  $("#prewarm-text").textContent = all ? `All ${s.total} lessons ready`
    : `${s.ready}/${s.total} lessons ready${s.running ? " · building the rest…" : s.failed ? " · the rest build when you open them" : ""}`;
  $("#prewarm-fill").style.width = `${s.total ? Math.round((100 * s.ready) / s.total) : 0}%`;
  $("#prewarm").classList.toggle("all", all);
  if (s.running) prewarmTimer = setTimeout(pollPrewarm, 3000);
}

// ---------- lesson ----------
function liveThought(text) {
  Yaad?.say(`💭 ${text}`, 0);
  $("#lesson-thought").textContent = text;
}

async function openLesson(day, opts = {}) {
  const seq = ++state.openSeq;                  // only the latest request may render
  state.lessonFrom = "plan";
  Yaad?.think(true); Yaad?.say("Reading your saves and writing your lesson…", 0);
  stopPlayer();
  show("lesson");
  $("#lesson").hidden = true; $("#lesson-error").hidden = true; $("#lesson-loading").hidden = false;
  $("#lesson-thought").textContent = "";
  try {
    const lesson = await streamApi("/api/lesson/stream", { day, ...opts }, (t) => { if (seq === state.openSeq) liveThought(t); });
    if (seq !== state.openSeq) return;
    state.lesson = lesson; state.level = lesson.level; state.day = lesson.day;
    renderLesson(lesson);
    Yaad?.think(false);
    if (state.lastView === "lesson") Yaad?.say("Here's today's lesson! Tap 👍/👎 so I learn what works for you.", 5000);
  } catch (e) {
    if (seq !== state.openSeq) return;
    $("#lesson-error").textContent = e.message; $("#lesson-error").hidden = false;
  } finally {
    if (seq === state.openSeq) {
      $("#lesson-loading").hidden = true;
      if (!$("#lesson-error").hidden) { Yaad?.think(false); Yaad?.mood("sad", 3000); Yaad?.say("Oops, that didn't work. Try again?", 4000); }
    }
  }
}

function paragraphs(body) {
  const out = [];
  for (const block of String(body).split(/\n\s*\n/)) {
    const lines = block.split("\n").map((l) => l.trim()).filter(Boolean);
    if (!lines.length) continue;
    if (lines.every((l) => /^[-•*]\s+/.test(l))) out.push(el("ul", {}, ...lines.map((l) => el("li", {}, l.replace(/^[-•*]\s+/, "")))));
    else out.push(el("p", {}, lines.join(" ")));
  }
  return out;
}

async function renderLesson(l) {
  $("#lesson-meta").textContent = `Day ${l.day} · ${DEPTH[l.level]} (level ${l.level}) · ${l.words} words`;
  $("#lesson-title").textContent = l.title;
  renderJudge(l);
  $("#lesson-sections").replaceChildren(...l.sections.map((s, i) => el("div", { class: "section" }, sectionHead(s, i), ...paragraphs(s.body))));

  stopPlayer();
  const wantDiagram = state.formats.includes("diagram") && !!l.mermaid;
  $("#diagram-box").hidden = !wantDiagram;
  $("#diagram").replaceChildren();
  if (wantDiagram && window.mermaid) {
    try {
      const { svg } = await mermaid.render(`m${Date.now()}`, l.mermaid);
      if (state.lesson !== l) return;           // a newer lesson took over while rendering
      $("#diagram").innerHTML = svg;            // strict-mode SVG built from sanitised labels
    } catch { $("#diagram-box").hidden = true; }
  }
  $("#player").hidden = !(state.formats.includes("animation") && (l.animation || []).length > 1 && !$("#diagram-box").hidden);
  $("#quiz-box").hidden = !state.formats.includes("quiz");
  stopListen();
  $("#listen-bar").hidden = !state.formats.includes("audio");
  loadVisuals(l);

  const extra = (l.extra || []).filter(Boolean);
  $("#extra-box").hidden = !extra.length;
  $("#extra").replaceChildren(...extra.map((x) => el("li", {}, x)));

  $("#quiz").replaceChildren(...(l.quiz || []).map((q) => {
    const why = el("div", { class: "why" });
    const opts = q.options.map((o, i) => el("button", { class: "opt", onclick: (ev) => {
      opts.forEach((b, j) => { b.disabled = true; if (j === q.answer_index) b.classList.add("right"); });
      if (i !== q.answer_index) ev.currentTarget.classList.add("wrong");
      why.textContent = (i === q.answer_index ? "Right! " : "Not quite. ") + (q.why || "");
      sendFeedback(i === q.answer_index ? "quiz_right" : "quiz_wrong", "", q.q).catch(() => {});
      quizTally(l, i === q.answer_index);
      if (i === q.answer_index) { Yaad?.mood("happy"); Yaad?.say(["Yes! 🎉", "Nailed it!", "You remembered! ✨"][Math.floor(Math.random() * 3)], 2500); }
      else { Yaad?.mood("sad", 2000); Yaad?.say("Close! Read why below 👇", 2500); }
      if (state.day && !state.doneDays.includes(state.day)) state.doneDays.push(state.day);
    } }, o));
    return el("div", { class: "q" }, el("p", {}, q.q), ...opts, why);
  }));

  $("#sources").replaceChildren(...l.source_cards.map((s) => el("div", { class: "src" },
    el("span", { class: "tag" }, s.platform),
    s.url ? el("a", { href: s.url, target: "_blank", rel: "noopener noreferrer" }, s.title) : el("b", {}, s.title),
    s.note ? el("div", { class: "note" }, `Your note: ${s.note}`) : "",
    el("div", { class: "note" }, s.summary),
    el("div", { class: "src-meta" }, timeChip(s.minutes), keptBadge(s)))));

  $("#done-card").hidden = true; state.quiz = { right: 0, answered: 0 };
  resetTeach(l);
  document.querySelectorAll(".fb").forEach((b) => b.classList.remove("on"));
  $("#fb-result").textContent = ""; $("#regen").hidden = true;
  $("#lesson").hidden = false;
  refreshSpend();
}

// ---------- visuals: real figure (SAM 3.1) + simple picture (Muse Image) ----------
function setFigure(id, item) {
  const fig = $(id), img = fig.querySelector("img"), shimmer = fig.querySelector(".shimmer");
  fig.hidden = false;
  if (!item) { shimmer.hidden = false; img.classList.remove("ready"); return; }
  img.onload = () => { shimmer.hidden = true; img.classList.add("ready"); };
  img.src = item.url;
}

function layoutSee() {
  const visible = [$("#fig-real"), $("#fig-simple")].filter((f) => !f.hidden).length;
  $(".see").classList.toggle("single", visible === 1);
}

async function loadVisuals(l) {
  const wantFigure = state.formats.includes("figure"), wantPicture = state.formats.includes("picture");
  $("#see-box").hidden = !(wantFigure || wantPicture);
  $("#fig-real").hidden = true; $("#fig-simple").hidden = true; $("#see-note").textContent = "";
  $("#fig-real .whole").hidden = true; $("#fig-real .full").hidden = true;
  if ($("#see-box").hidden) return;
  if (wantFigure) setFigure("#fig-real", null);
  if (wantPicture) setFigure("#fig-simple", null);
  layoutSee();
  $("#see-note").textContent = [wantFigure && "SAM 3.1 is finding the figure in your save", wantPicture && "Muse Image is drawing…"].filter(Boolean).join(" · ");
  try {
    const v = await api("/api/visuals", { lesson_id: l.id });
    if (state.lesson !== l) return;            // the learner moved on
    if (v.figure) {
      setFigure("#fig-real", v.figure);
      const full = $("#fig-real .full");
      full.href = `/media/${v.figure.full_slide}`; full.hidden = false;
      $("#fig-real .label").textContent = `From your save: ${v.figure.title || ""}`;
      $("#fig-real .whole").hidden = !!v.figure.cropped;
    } else $("#fig-real").hidden = true;
    if (v.picture) setFigure("#fig-simple", v.picture); else $("#fig-simple").hidden = true;
    $("#see-box").hidden = !v.figure && !v.picture;
    $("#see-note").textContent = "";
    layoutSee();
  } catch (e) {
    if (state.lesson === l) $("#see-box").hidden = true;
  }
}

// ---------- animated walk-through of the diagram (our player; the model only supplies steps) ----------
// player.run increments on every start/stop, so callbacks from an old run (speech end, timers) do nothing.
const player = { timer: null, run: 0, i: 0, playing: false };

function nodeEl(safeId) {
  return [...document.querySelectorAll("#diagram svg g.node")].find((g) => g.id.startsWith(`flowchart-${safeId}-`));
}

function stopPlayer() {
  player.run += 1; player.playing = false;
  clearTimeout(player.timer); player.timer = null;
  releaseSpeech("player");
  $("#diagram").classList.remove("playing");
  document.querySelectorAll("#diagram g.node.lit").forEach((g) => g.classList.remove("lit"));
  $("#caption").hidden = true;
  setBtn($("#play"), "play_arrow", "Play");
}

function finishPlayer() {
  player.playing = false; clearTimeout(player.timer); player.timer = null;
  releaseSpeech("player");
  $("#diagram").classList.remove("playing");
  document.querySelectorAll("#diagram g.node.lit").forEach((g) => g.classList.remove("lit"));
  setBtn($("#play"), "replay", "Replay");        // caption stays on the last step until replay or leaving
}

function playStep(run) {
  if (run !== player.run) return;
  const steps = state.lesson.animation;
  if (player.i >= steps.length) { finishPlayer(); return; }
  const step = steps[player.i];
  document.querySelectorAll("#diagram g.node.lit").forEach((g) => g.classList.remove("lit"));
  const node = nodeEl(step.node);
  if (node) node.classList.add("lit");
  $("#caption").textContent = `${player.i + 1}/${steps.length} · ${step.narration}`;
  const readMs = Math.max(2200, step.narration.split(" ").length * 330);
  let advanced = false;
  const next = () => { if (advanced || run !== player.run) return; advanced = true; clearTimeout(player.timer); player.i += 1; playStep(run); };
  if ($("#speak").checked && window.speechSynthesis) {
    const u = makeUtterance(step.narration);
    u.onend = () => { if (run === player.run) player.timer = setTimeout(next, 400); };
    u.onerror = u.onend;                         // a failed or interrupted voice must not stall the player
    speechSynthesis.speak(u);
    player.timer = setTimeout(next, readMs * 3); // backstop if no speech event ever arrives
  } else {
    player.timer = setTimeout(next, readMs);
  }
}

$("#play").addEventListener("click", () => {
  if (player.playing) { stopPlayer(); return; }
  stopPlayer();
  claimSpeech("player");
  player.i = 0; player.playing = true;
  $("#diagram").classList.add("playing");
  $("#caption").hidden = false;
  setBtn($("#play"), "stop", "Stop");
  $("#caption").scrollIntoView({ block: "center", behavior: "smooth" });
  playStep(player.run);
});

document.querySelectorAll(".fb").forEach((b) => b.addEventListener("click", async () => {
  document.querySelectorAll(".fb").forEach((x) => x.classList.toggle("on", x === b));
  const { level } = await sendFeedback(b.dataset.kind);
  if (level !== state.level) {
    $("#fb-result").textContent = `Got it — I'll teach this topic at "${DEPTH[level]}" from now on.`;
    const regen = $("#regen");
    regen.textContent = `Redo this lesson as "${DEPTH[level]}"`;
    regen.hidden = false;
    regen.onclick = () => openLesson(state.day, { level });
  } else {
    $("#fb-result").textContent = "Thanks — keeping this level.";
  }
}));

async function refreshSpend() {
  if (!DEMO) return;
  try { const s = await api("/api/spend"); $("#spend").textContent = `spent $${s.total_usd.toFixed(2)}`; } catch {}
}

// ---------- one voice at a time: lesson Listen, diagram narration and answers share speechSynthesis ----------
const speech = { owner: null };
function claimSpeech(owner) {
  if (speech.owner && speech.owner !== owner) {
    ({ player: stopPlayer, listen: stopListen, answer: stopAnswerSpeech })[speech.owner]?.();
  }
  speech.owner = owner;
}
function releaseSpeech(owner) {
  if (speech.owner !== owner) return;
  speech.owner = null;
  if (window.speechSynthesis) speechSynthesis.cancel();
}
function say(text, run, isCurrent, onDone) {
  const u = makeUtterance(text);
  let done = false;
  const finish = () => { if (done) return; done = true; clearTimeout(backstop); if (isCurrent(run)) onDone(); };
  u.onend = finish; u.onerror = finish;               // a failed voice must not stall what comes next
  const backstop = setTimeout(finish, Math.max(4000, text.split(/\s+/).length * 600));
  speechSynthesis.speak(u);
}

// ---------- 🔊 Listen to the lesson ----------
const listen = { run: 0, playing: false };
function stopListen() {
  listen.run += 1; listen.playing = false;
  releaseSpeech("listen");
  document.querySelectorAll(".section.reading").forEach((s) => s.classList.remove("reading"));
  setBtn($("#listen"), "volume_up", "Listen"); $("#listen-status").textContent = "";
}
$("#listen").addEventListener("click", () => {
  if (listen.playing) { stopListen(); return; }
  if (!window.speechSynthesis) { $("#listen-status").textContent = "This browser can't read aloud."; return; }
  claimSpeech("listen");
  speechSynthesis.cancel();
  const l = state.lesson, run = ++listen.run;
  listen.playing = true;
  setBtn($("#listen"), "stop", "Stop");
  const nodes = [...document.querySelectorAll("#lesson-sections .section")];
  const parts = [{ text: l.title, node: null }, ...l.sections.map((s, i) => ({
    text: `${s.heading ? s.heading + ". " : ""}${String(s.body).replace(/^\s*[-•*]\s+/gm, "")}`, node: nodes[i] }))];
  const step = (i) => {
    document.querySelectorAll(".section.reading").forEach((s) => s.classList.remove("reading"));
    if (i >= parts.length) { stopListen(); return; }
    if (parts[i].node) { parts[i].node.classList.add("reading"); parts[i].node.scrollIntoView({ block: "nearest", behavior: "smooth" }); }
    $("#listen-status").textContent = `Reading ${i + 1}/${parts.length}`;
    say(parts[i].text, run, (r) => r === listen.run, () => step(i + 1));
  };
  step(0);
});

// ---------- 🎙 Ask your saves: record WAV (16 kHz mono 16-bit, what Muse Voice Transcribe takes) ----------
const TARGET_RATE = 16000, MAX_REC_SECONDS = 30;
const rec = { target: "ask", btn: null, status: null, lessonSeq: 0, ctx: null, stream: null, node: null, chunks: [], rate: TARGET_RATE, timer: null, started: 0, busy: false, gen: 0 };

function encodeWav(samples, rate) {
  const buf = new ArrayBuffer(44 + samples.length * 2), v = new DataView(buf);
  const str = (o, s) => [...s].forEach((c, i) => v.setUint8(o + i, c.charCodeAt(0)));
  str(0, "RIFF"); v.setUint32(4, 36 + samples.length * 2, true); str(8, "WAVE"); str(12, "fmt ");
  v.setUint32(16, 16, true); v.setUint16(20, 1, true); v.setUint16(22, 1, true); v.setUint32(24, rate, true);
  v.setUint32(28, rate * 2, true); v.setUint16(32, 2, true); v.setUint16(34, 16, true); str(36, "data");
  v.setUint32(40, samples.length * 2, true);
  samples.forEach((s, i) => v.setInt16(44 + i * 2, Math.max(-1, Math.min(1, s)) * 0x7fff, true));
  return new Blob([buf], { type: "audio/wav" });
}

function resample(input, from, to) {
  if (from === to) return input;
  const out = new Float32Array(Math.floor(input.length * to / from));
  for (let i = 0; i < out.length; i++) {
    const x = i * from / to, i0 = Math.floor(x), i1 = Math.min(i0 + 1, input.length - 1);
    out[i] = input[i0] + (input[i1] - input[i0]) * (x - i0);
  }
  return out;
}

async function startRecording(target = "ask") {
  if (rec.busy || rec.ctx) return;
  rec.target = target; rec.lessonSeq = state.openSeq;
  rec.btn = target === "teach" ? $("#teach-mic") : $("#mic");
  rec.status = target === "teach" ? $("#teach-status") : $("#mic-status");                    // a second tap while the permission prompt is up
  rec.busy = true;
  stopPlayer(); stopListen(); stopAnswerSpeech();     // never record the app's own voice
  if (window.speechSynthesis) speechSynthesis.cancel();
  try {
    rec.stream = await navigator.mediaDevices.getUserMedia({ audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true } });
    rec.ctx = new AudioContext({ sampleRate: TARGET_RATE });
    rec.rate = rec.ctx.sampleRate;                    // some browsers ignore the request; resample later
    const source = rec.ctx.createMediaStreamSource(rec.stream);
    rec.node = rec.ctx.createScriptProcessor(4096, 1, 1);
    rec.chunks = [];
    rec.node.onaudioprocess = (e) => rec.chunks.push(new Float32Array(e.inputBuffer.getChannelData(0)));
    source.connect(rec.node); rec.node.connect(rec.ctx.destination);
  } catch {
    rec.stream?.getTracks().forEach((t) => t.stop());
    await rec.ctx?.close().catch(() => {});
    rec.ctx = null; rec.stream = null; rec.node = null; rec.busy = false;
    rec.status.textContent = "Mic not available or blocked — type your question instead.";
    return;
  }
  rec.busy = false;
  rec.gen = askGen;
  rec.started = performance.now();
  rec.btn.classList.add("rec"); rec.btn.setAttribute("aria-label", "Stop recording");
  rec.timer = setInterval(() => {
    const s = Math.floor((performance.now() - rec.started) / 1000);
    rec.status.textContent = `Listening… ${s}s (tap to stop)`;
    if (s >= MAX_REC_SECONDS) stopRecording();
  }, 250);
}

async function stopRecording(discard = false) {
  if (!rec.ctx) return;
  clearInterval(rec.timer);
  rec.btn.classList.remove("rec"); rec.btn.setAttribute("aria-label", "Record a question");
  rec.node?.disconnect(); rec.stream?.getTracks().forEach((t) => t.stop());
  const rate = rec.rate, chunks = rec.chunks, gen = rec.gen, ctx = rec.ctx;
  rec.ctx = null; rec.stream = null; rec.node = null; rec.chunks = [];
  await ctx.close().catch(() => {});
  if (discard) { rec.status.textContent = "Tap to speak"; return; }
  const total = chunks.reduce((n, c) => n + c.length, 0);
  if (total < rate * 0.5) { rec.status.textContent = "That was too short — tap and ask again."; return; }
  const all = new Float32Array(total); let o = 0;
  chunks.forEach((c) => { all.set(c, o); o += c.length; });
  const wav = encodeWav(resample(all, rate, TARGET_RATE), TARGET_RATE);
  rec.status.textContent = "Muse Voice Transcribe is listening…";
  rec.btn.disabled = true;
  const seqAtSend = askSeq;
  try {
    const form = new FormData(); form.append("audio", wav, "question.wav");
    const res = await fetch("/api/transcribe", { method: "POST", body: form, headers: { "X-Yaadsi": "1" }, signal: askAbort.signal });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(typeof data.detail === "string" ? data.detail : "Couldn't transcribe that.");
    if (rec.target === "teach") {                       // teach-back: fill the box, the learner presses Check
      if (rec.lessonSeq !== state.openSeq) return;
      const box = $("#teach-text"); box.value = (box.value ? box.value + " " : "") + data.text;
      box.dispatchEvent(new Event("input"));
      rec.status.textContent = "Got it. Edit if you like, then press Check.";
      return;
    }
    if (gen !== askGen || seqAtSend !== askSeq) return;   // panel closed, or a typed question went first
    $("#ask-q").value = data.text;
    rec.status.textContent = "Got it.";
    askQuestion(data.text, { fromMic: true });
  } catch (e) {
    if (e.name !== "AbortError" && gen === askGen) rec.status.textContent = e.message;
  } finally { rec.btn.disabled = false; }
}

$("#mic").addEventListener("click", () => (rec.ctx ? stopRecording() : startRecording("ask")));
$("#teach-mic").addEventListener("click", () => (rec.ctx ? stopRecording() : startRecording("teach")));

const answerSpeech = { run: 0 };
function stopAnswerSpeech() { answerSpeech.run += 1; releaseSpeech("answer"); }
$("#ask-speak").addEventListener("change", () => { if (!$("#ask-speak").checked) stopAnswerSpeech(); });

// askGen changes when the panel closes, so late transcripts and answers are dropped (not spoken).
let askSeq = 0, askGen = 0, askAbort = new AbortController();
async function askQuestion(q, { fromMic = false } = {}) {
  q = (q || "").trim();
  if (q.length < 2) return;
  const seq = ++askSeq, gen = askGen;
  stopAnswerSpeech();
  if (!fromMic) $("#mic-status").textContent = "Tap to speak";
  $("#ask-answer").hidden = false; $("#ask-follow").hidden = true;
  $("#ask-text").textContent = "Muse Spark Open is searching your saves…"; $("#ask-sources").replaceChildren();
  Yaad?.think(true);
  $("#ask-badge").hidden = true;
  try {
    const a = await streamApi("/api/ask/stream", { question: q }, (t) => {
      if (seq !== askSeq || gen !== askGen) return;
      $("#ask-text").textContent = `💭 ${t}`; Yaad?.say(`💭 ${t}`, 0);
    }, askAbort.signal);
    if (seq !== askSeq || gen !== askGen) return;
    Yaad?.think(false);
    $("#ask-text").textContent = a.answer;
    $("#ask-sources").replaceChildren(...a.source_cards.map((s) => el("div", { class: "src" },
      el("span", { class: "tag" }, s.platform),
      s.url ? el("a", { href: s.url, target: "_blank", rel: "noopener noreferrer" }, s.title) : el("b", {}, s.title))));
    if (a.found && a.follow_up) { $("#ask-follow").dataset.q = a.follow_up; $("#ask-follow").replaceChildren(icon("arrow_forward"), a.follow_up); $("#ask-follow").hidden = false; }
    $("#ask-badge").hidden = a.mode !== "whole";
    $("#ask-badge").replaceChildren(icon("menu_book"), `Read all ${a.saves_read} of your saves`);
    if ($("#ask-speak").checked && window.speechSynthesis && !$("#ask-panel").hidden) {
      claimSpeech("answer"); speechSynthesis.cancel();
      const run = ++answerSpeech.run;
      say(a.answer, run, (r) => r === answerSpeech.run, () => releaseSpeech("answer"));
    }
    refreshSpend();
  } catch (e) { if (e.name !== "AbortError" && seq === askSeq && gen === askGen) $("#ask-text").textContent = e.message; }
  finally { if (seq === askSeq) Yaad?.think(false); }
}

$("#ask-form").addEventListener("submit", (e) => { e.preventDefault(); askQuestion($("#ask-q").value); });
$("#ask-follow").addEventListener("click", () => { const q = $("#ask-follow").dataset.q || ""; $("#ask-q").value = q; askQuestion(q); });
function setAskOpen(open) {
  $("#ask-panel").hidden = !open; $("#ask-open").setAttribute("aria-expanded", String(open));
  document.body.classList.toggle("ask-open", open);
  if (open) { $("#ask-q").focus(); return; }
  askGen += 1; askSeq += 1;                            // drop anything still in flight
  Yaad?.think(false);
  askAbort.abort(); askAbort = new AbortController();
  stopRecording(true); stopAnswerSpeech();
  $("#mic").disabled = false;
  if ($("#ask-text").textContent.includes("searching")) $("#ask-answer").hidden = true;
}
$("#ask-open").addEventListener("click", () => setAskOpen($("#ask-panel").hidden));
$("#ask-close").addEventListener("click", () => setAskOpen(false));

// ---------- feedback -> learned style rules (Muse Wonder) ----------
let learnerUpdates = 0;
async function sendFeedback(kind, section = "", detail = "") {
  if (!state.lesson) return null;
  const r = await api("/api/feedback", { kind, lesson_id: state.lesson.id, section, detail });
  if (r.learning) watchForNewRules();
  return r;
}

function toast(text) {
  const t = el("div", { class: "toast", role: "status" }, text);
  document.body.append(t);
  setTimeout(() => t.remove(), 6500);
}

async function watchForNewRules() {
  for (let i = 0; i < 8; i++) {                 // rule learning runs in the background (~5 s)
    await new Promise((r) => setTimeout(r, 2500));
    const l = await api("/api/learner").catch(() => null);
    if (l && l.updates > learnerUpdates) {
      learnerUpdates = l.updates;
      const fresh = l.new_rules.length ? l.new_rules : l.rules;
      toast(`🧠 New about you: ${fresh[0] || l.summary}`);
      Yaad?.mood("learned", 6000);
      Yaad?.say(`I noticed something about you: ${fresh[0] || l.summary}`, 6000);
      renderLearner(l);
      return;
    }
  }
}

function renderLearner(l) {
  learnerUpdates = l.updates;
  const empty = !l.feedback;
  $("#learner-meta").textContent = empty ? "" : `${l.feedback} feedback · next update in ${l.next_update_in}`;
  $("#learner-summary").textContent = empty
    ? "Nothing yet. Rate a few sections or answer quizzes and I'll learn how you like to learn."
    : (l.summary || `Learning… ${l.next_update_in} more feedback until my first rules about you.`);
  $("#learner-summary").classList.toggle("muted", !empty && !!l.summary);
  $("#learner-rules").hidden = !l.rules.length;
  $("#learner-rules").replaceChildren(...l.rules.map((r) => el("li", {}, r, l.new_rules.includes(r) ? el("span", { class: "badge" }, "new") : "")));
  const levels = Object.entries(l.levels);
  $("#learner-levels").hidden = !levels.length;
  $("#learner-levels").replaceChildren(...levels.map(([t, v]) => el("span", { class: "chip" }, `${t}: ${DEPTH[v]}`)));
}

async function loadLearner() {
  try { renderLearner(await api("/api/learner")); } catch {}
}

function sectionHead(s, i) {
  const thumbs = el("div", { class: "thumbs" });
  for (const [kind, label, name] of [["thumbs_up", "Helpful", "thumb_up"], ["thumbs_down", "Not helpful", "thumb_down"]]) {
    thumbs.append(el("button", { class: "thumb", "aria-label": `${label}: this section`, title: label, onclick: async (ev) => {
      thumbs.querySelectorAll(".thumb").forEach((b) => b.classList.toggle("on", b === ev.currentTarget));
      await sendFeedback(kind, s.heading || `section ${i + 1}`);
    } }, icon(name)));
  }
  return el("div", { class: "sec-head" }, el("h2", {}, s.heading || ""), thumbs);
}

const DRAFT_STYLE = (t) => (t <= 0.5 ? "careful" : t <= 0.85 ? "balanced" : "creative");
function renderJudge(l) {
  const j = l.judge;
  $("#judge-box").hidden = !j || !j.drafts || j.drafts < 2;
  if ($("#judge-box").hidden) return;
  const ids = Object.keys(j.scores || {});
  const name = (id) => { const k = ids.indexOf(id); const t = (j.temperatures || [])[k]; return `Draft ${k + 1}${t != null ? ` (${DRAFT_STYLE(t)})` : ""}`; };
  $("#judge-summary").textContent = `Best of ${j.drafts} drafts, picked by Muse Wonder: ${name(j.best)}`;
  $("#judge-why").textContent = (j.why || "").replace(/\bD(\d)\b/g, "Draft $1");
  $("#judge-scores").replaceChildren(el("table", { class: "scores" },
    el("tr", {}, el("th", {}, ""), el("th", {}, "grounding"), el("th", {}, "level fit"), el("th", {}, "clarity")),
    ...ids.map((id) => { const sc = j.scores[id] || {};
      return el("tr", { class: id === j.best ? "chosen" : "" }, el("td", {}, name(id) + (id === j.best ? " ✓" : "")),
        el("td", {}, sc.grounding ?? "–"), el("td", {}, sc.level_fit ?? "–"), el("td", {}, sc.clarity ?? "–")); })));
}

// ---------- blind test ----------
const evalState = { pairs: [], picks: {}, i: 0 };

function lessonColumn(label, l) {
  const col = [el("span", { class: "label" }, label), el("h3", {}, l.title || "")];
  for (const s of l.sections || []) col.push(el("h4", {}, s.heading || ""), ...paragraphs(s.body));
  if ((l.quiz || []).length) col.push(el("div", { class: "qs" }, el("b", {}, "Quiz: "), l.quiz.map((q) => q.q).join(" · ")));
  return col;
}

async function loadEval() {
  const data = await api("/api/eval");
  evalState.pairs = data.pairs; evalState.picks = data.picks;
  $("#eval-empty").hidden = data.pairs.length > 0;
  if (!data.pairs.length) { $("#eval-pair").hidden = true; $("#eval-results").hidden = true; return; }
  if (data.done) { renderEvalResults(data.results); return; }
  evalState.i = data.pairs.findIndex((p) => !(p.id in data.picks));
  renderPair();
}

function renderPair() {
  const p = evalState.pairs[evalState.i];
  $("#eval-results").hidden = true; $("#eval-pair").hidden = false;
  $("#eval-count").textContent = `Pair ${evalState.i + 1} of ${evalState.pairs.length}`;
  $("#eval-source").textContent = `From your save: ${p.save_title} (${p.topic})`;
  $("#eval-A").replaceChildren(...lessonColumn("A", p.A));
  $("#eval-B").replaceChildren(...lessonColumn("B", p.B));
  const prev = evalState.pairs[evalState.i - 1];
  $("#eval-back").hidden = !prev;
  document.querySelectorAll(".pick").forEach((x) => x.classList.toggle("was", evalState.picks[p.id] === x.dataset.choice));
  window.scrollTo(0, 0);
}

$("#eval-back").addEventListener("click", () => { if (evalState.i > 0) { evalState.i -= 1; renderPair(); } });

document.querySelectorAll(".pick").forEach((b) => b.addEventListener("click", async () => {
  const p = evalState.pairs[evalState.i];
  document.querySelectorAll(".pick").forEach((x) => { x.disabled = true; });
  try {
    const r = await api("/api/eval/pick", { id: p.id, choice: b.dataset.choice });
    evalState.picks[p.id] = b.dataset.choice;
    if (r.done) { await loadEval(); return; }
    const next = evalState.pairs.findIndex((x, k) => k > evalState.i && !(x.id in evalState.picks));
    evalState.i = next >= 0 ? next : evalState.pairs.findIndex((x) => !(x.id in evalState.picks));
    renderPair();
  } finally { document.querySelectorAll(".pick").forEach((x) => { x.disabled = false; }); }
}));

function barRow(name, t) {
  const pct = (n) => (t.n ? (100 * n) / t.n : 0);
  const seg = (cls, n, label) => (n ? el("span", { class: cls, style: `width:${pct(n)}%` }, `${label} ${n}`) : "");
  return el("div", { class: "bar-row" }, el("b", {}, `${name} (${t.n})`),
    el("div", { class: "bar", role: "img", "aria-label": `${name}: full system ${t.system}, plain ${t.single}, tie ${t.tie}` },
      seg("sys", t.system, "System"), seg("tie", t.tie, "Tie"), seg("plain", t.single, "Plain")));
}

function renderEvalResults(r) {
  $("#eval-pair").hidden = true; $("#eval-results").hidden = false;
  $("#eval-def").textContent = r.rules.length
    ? `Full system = best of 3 Muse Spark Open drafts picked by Muse Wonder, using the style rules learned from your feedback: ${r.rules.join("; ")}.`
    : "Full system = best of 3 Muse Spark Open drafts picked by Muse Wonder. No style rules had been learned yet when this round was built, so this measures best-of-3 only.";
  $("#eval-chart").replaceChildren(
    el("div", { class: "bars" }, barRow("You (blind)", r.human), barRow("Muse Wonder (both orders)", r.wonder)),
    el("div", { class: "legend" }, el("span", {}, el("i", { style: "background:#2f6fde" }), "Full system wins"),
      el("span", {}, el("i", { style: "background:#c9a227" }), "Tie"), el("span", {}, el("i", { style: "background:#9aa1ad" }), "Plain draft wins")));
  $("#eval-agree").textContent = `You and Muse Wonder agreed on ${r.agreement.agree} of ${r.agreement.n} pairs. `
    + `Small sample (${r.pairs} pairs). Wonder also picked the full system's best draft, so its vote is not independent; `
    + "it judged each pair in both orders and only counts a win when both agree. Your blind picks are the independent signal.";
  const label = { system: "Full system", single: "Plain draft", tie: "Tie" };
  $("#eval-rows").replaceChildren(...r.rows.map((row) => el("div", { class: "eval-row" },
    el("b", {}, row.title), ` · full system was ${row.system_side} · you: ${label[row.human] || "-"} · Wonder: ${label[row.wonder]}`,
    el("div", { class: "muted small" }, row.wonder_why))));
}


// ---------- Yaad's voice: pick a natural (neural) voice, prefer a friendly female one; user can change it ----------
const VOICE_KEY = "yaad-voice";
const PREFERRED = [/ava.*natural/i, /emma.*natural/i, /jenny.*natural/i, /aria.*natural/i, /michelle.*natural/i,
  /natural/i, /\b(ava|emma|jenny|aria|zira|samantha|female|woman)\b/i];
const voice = { list: [], settings: { name: "", rate: 1.0, pitch: 1.05 } };
try { Object.assign(voice.settings, JSON.parse(localStorage.getItem(VOICE_KEY) || "{}")); } catch {}

function loadVoices() {
  if (!window.speechSynthesis) return;
  voice.list = speechSynthesis.getVoices().filter((v) => /^en/i.test(v.lang));
  const sel = $("#voice-name");
  if (!sel) return;
  const natural = (v) => /natural|neural/i.test(v.name);
  const sorted = [...voice.list].sort((a, b) => natural(b) - natural(a) || a.name.localeCompare(b.name));
  sel.replaceChildren(...sorted.map((v) => {
    const o = el("option", { value: v.name }, `${natural(v) ? "✨ " : ""}${v.name.replace(/^Microsoft /, "").replace(/ - .*$/, "")} (${v.lang})`);
    return o;
  }));
  sel.value = pickVoice()?.name || "";
}
function pickVoice() {
  const byName = voice.list.find((v) => v.name === voice.settings.name);
  if (byName) return byName;
  for (const re of PREFERRED) { const v = voice.list.find((x) => re.test(x.name)); if (v) return v; }
  return voice.list[0] || null;
}
// Speak like a friend, not a document: drop list markers and headings, turn line breaks into pauses.
function casual(text) {
  return String(text).replace(/^\s*[-•*#]+\s*/gm, "").replace(/\n+/g, ". ").replace(/\.\s*\./g, ".").replace(/\s{2,}/g, " ").trim();
}
function makeUtterance(text) {
  const u = new SpeechSynthesisUtterance(casual(text));
  const v = pickVoice();
  if (v) { u.voice = v; u.lang = v.lang; }
  u.rate = voice.settings.rate; u.pitch = voice.settings.pitch;
  return u;
}
function saveVoice() {
  voice.settings = { name: $("#voice-name").value, rate: +$("#voice-rate").value, pitch: +$("#voice-pitch").value };
  try { localStorage.setItem(VOICE_KEY, JSON.stringify(voice.settings)); } catch {}
}
if (window.speechSynthesis) { loadVoices(); speechSynthesis.addEventListener("voiceschanged", loadVoices); }
document.querySelectorAll(".voice-open").forEach((b) => b.addEventListener("click", () => {
  loadVoices();
  $("#voice-rate").value = voice.settings.rate; $("#voice-pitch").value = voice.settings.pitch;
  $("#voice-dlg").showModal();
}));
for (const id of ["#voice-name", "#voice-rate", "#voice-pitch"]) $(id).addEventListener("change", saveVoice);
$("#voice-test").addEventListener("click", () => {
  saveVoice();
  stopPlayer(); stopListen(); stopAnswerSpeech(); speechSynthesis.cancel();
  speechSynthesis.speak(makeUtterance("Hey! I'm Yaad. This is how I sound. Want to learn something cool today?"));
});
$("#voice-close").addEventListener("click", () => $("#voice-dlg").close());


// ---------- finishing a day: a short cheer, go on to the next day, discover related saves ----------
const CHEERS = ["Nice work! 🎉", "You nailed it! ✨", "Another one in your brain! 🧠", "Look at you go! 🚀"];
function quizTally(l, right) {
  state.quiz.answered += 1; if (right) state.quiz.right += 1;
  if (state.quiz.answered === (l.quiz || []).length) showDone(l);
}

async function showDone(l) {
  const { right, answered } = state.quiz;
  const plan = state.plan, days = plan?.days || [];
  const isDay = !l.mini && l.day;
  if (!isDay) return showMiniDone(l, right, answered);   // Today's pick or a mini lesson: no plan talk
  if (isDay && !state.doneDays.includes(l.day)) state.doneDays.push(l.day);
  const next = isDay ? days.find((d) => d.day > l.day && !state.doneDays.includes(d.day)) : days.find((d) => !state.doneDays.includes(d.day));
  $("#done-title").textContent = isDay ? `Day ${l.day} done! ${CHEERS[Math.floor(Math.random() * CHEERS.length)]}` : `Mini lesson done! ${CHEERS[0]}`;
  $("#done-sub").textContent = `${right}/${answered} right${right === answered ? ". Perfect score!" : ". Nice work."}`;
  $("#done-next").replaceChildren(next
    ? el("div", { class: "next-row" },
        el("button", { class: "primary", type: "button", onclick: () => openLesson(next.day) }, `Next: Day ${next.day} →`),
        el("span", { class: "muted small" }, `${next.titles.join(" · ")} · or come back tomorrow`))
    : el("p", {}, el("b", {}, days.length && isDay ? "Plan complete! 🏁 You covered everything you planned." : "")));
  $("#done-card").hidden = false;
  confetti($("#done-card"));
  Yaad?.mood("happy", 3000);
  Yaad?.say(next ? `${isDay ? `Day ${l.day}` : "That"} done! Ready for more?` : "You finished the whole plan! 🏁", 5000);
  $("#done-card").scrollIntoView({ behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth", block: "center" });
  loadDiscover(l);
}

function showMiniDone(l, right, answered) {
  const fromToday = state.lessonFrom === "today";
  $("#done-title").textContent = fromToday ? "Done for today 🎉" : `Mini lesson done! ${CHEERS[0]}`;
  $("#done-sub").textContent = `${right}/${answered} right${right === answered ? ". Perfect score!" : ". Nice work."}`;
  $("#done-next").replaceChildren(el("div", { class: "next-row" },
    el("button", { class: "primary", type: "button", onclick: () => show("setup") }, icon("today"), "Back to Today"),
    fromToday ? el("button", { class: "tonal", type: "button", onclick: () => { todayMore += 1; show("setup"); } }, icon("add"), "Do one more") : ""));
  $("#done-card").hidden = false;
  confetti($("#done-card"));
  Yaad?.mood("happy", 3000);
  Yaad?.say(fromToday ? "That's today's done. See you tomorrow!" : "Nice! That's one more thing you know.", 5000);
  $("#done-card").scrollIntoView({ behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth", block: "center" });
  loadDiscover(l);
}

async function loadDiscover(l) {
  $("#discover").replaceChildren(el("p", { class: "muted small" }, "Finding related saves…"));
  try {
    const { related } = await api(`/api/related?lesson_id=${encodeURIComponent(l.id)}`);
    if (state.lesson !== l) return;
    $("#discover").replaceChildren(...(related.length ? related.map((s) => {
      const [dot, kind] = (window.Brain?.PLATFORM || {})[s.platform] || ["#9aa1ad", s.platform];
      return el("div", { class: "disc" },
        el("div", { class: "disc-top" }, el("i", { style: `background:${dot}` }), el("span", { class: "muted small" }, kind), timeChip(s.minutes)),
        el("b", {}, s.title), el("div", { class: "muted small" }, s.why),
        s.note ? el("div", { class: "muted small" }, `Your note: ${s.note}`) : "",
        el("div", { class: "disc-actions" },
          el("button", { class: "chip", type: "button", onclick: () => openMini(s.id) }, "Mini lesson"),
          s.url ? el("a", { href: s.url, target: "_blank", rel: "noopener noreferrer", class: "small" }, "Open original ↗") : ""));
    }) : [el("p", { class: "muted small" }, "You've covered everything close to this. Try another topic in your brain!")]));
  } catch { $("#discover").replaceChildren(); }
}

async function openMini(saveId, { from = "mini" } = {}) {
  const ids = Array.isArray(saveId) ? saveId : [saveId];
  state.lessonFrom = from;
  const seq = ++state.openSeq;
  stopPlayer(); show("lesson");
  Yaad?.think(true); Yaad?.say("Making you a mini lesson…", 0);
  $("#lesson").hidden = true; $("#lesson-error").hidden = true; $("#lesson-loading").hidden = false;
  $("#lesson-thought").textContent = "";
  try {
    const lesson = await streamApi("/api/lesson/stream", { save_ids: ids }, (t) => { if (seq === state.openSeq) liveThought(t); });
    if (seq !== state.openSeq) return;
    state.lesson = lesson; state.level = lesson.level;
    renderLesson(lesson);
    $("#lesson-meta").textContent = `Mini lesson · ${DEPTH[lesson.level]} · ${lesson.words} words`;
  } catch (e) {
    if (seq === state.openSeq) { $("#lesson-error").textContent = e.message; $("#lesson-error").hidden = false; }
  } finally { if (seq === state.openSeq) { $("#lesson-loading").hidden = true; Yaad?.think(false); } }
}

function confetti(host) {
  if (matchMedia("(prefers-reduced-motion: reduce)").matches) return;
  const colors = ["#6c8cff", "#ff9f5a", "#e86bb7", "#3cc6a8", "#f2c94c"];
  for (let i = 0; i < 28; i++) {
    const c = el("i", { class: "confetti", style: `left:${Math.random() * 100}%;background:${colors[i % 5]};animation-delay:${Math.random() * 0.3}s;--dx:${(Math.random() - 0.5) * 160}px` });
    host.append(c); setTimeout(() => c.remove(), 1800);
  }
}

$("#done-brain").addEventListener("click", () => {
  const topic = state.plan?.topic || state.topic;
  show("setup");
  setTimeout(() => brain?.select(topic, { zoomIn: true }), 400);
});


// ---------- teach it back to Yaad (the Feynman step) ----------
const MIN_TEACH_WORDS = 15;
function resetTeach(l) {
  $("#teach-card").hidden = !l;
  if (!l) return;
  $("#teach-title").textContent = `Explain "${l.title}" to Yaad in your own words`;
  $("#teach-text").value = ""; $("#teach-result").hidden = true; $("#teach-status").textContent = "";
  $("#teach-go").disabled = true;
}
$("#teach-text").addEventListener("input", () => {
  const n = $("#teach-text").value.trim().split(/\s+/).filter(Boolean).length;
  $("#teach-go").disabled = n < MIN_TEACH_WORDS;
  $("#teach-count").textContent = n < MIN_TEACH_WORDS ? `${MIN_TEACH_WORDS - n} more words` : `${n} words`;
});
$("#teach-go").addEventListener("click", async () => {
  const l = state.lesson, seq = state.openSeq;
  if (!l) return;
  $("#teach-go").disabled = true; $("#teach-status").textContent = "Yaad is checking your explanation against your saves…";
  Yaad?.think(true);
  try {
    const r = await api("/api/teachback", { lesson_id: l.id, text: $("#teach-text").value });
    if (seq !== state.openSeq) return;
    $("#teach-status").textContent = "";
    $("#teach-score").textContent = r.score;
    $("#teach-ring").style.setProperty("--p", r.score);
    $("#teach-ring").className = `teach-ring ${r.verdict}`;
    $("#teach-verdict").textContent = { nailed: "You could teach this! 🏆", close: "Nearly there 💪", gaps: "Some gaps. Let's fix them 🧩" }[r.verdict];
    const list = (id, items) => { $(id).replaceChildren(...items.map((t) => el("li", {}, t))); $(id).parentElement.hidden = !items.length; };
    list("#teach-right", r.got_right); list("#teach-missed", r.missed);
    $("#teach-mis").hidden = !r.misconception; $("#teach-mis").textContent = r.misconception ? `💡 ${r.misconception}` : "";
    $("#teach-q").textContent = r.follow_up_question ? `🤔 ${r.follow_up_question}` : "";
    $("#teach-result").hidden = false;
    $("#teach-result").scrollIntoView({ block: "nearest", behavior: "smooth" });
    Yaad?.think(false);
    if (r.score >= 80) { Yaad?.mood("happy"); Yaad?.say(`${r.score}/100! You really get it. 🏆`, 4500); }
    else if (r.score >= 50) Yaad?.say(`${r.score}/100. Close! Check what you missed and try again.`, 4500);
    else { Yaad?.mood("sad", 2000); Yaad?.say(`${r.score}/100. No worries, re-read the missed bits and explain again.`, 4500); }
    if (r.passed) toast("🧠 That counts as a strong review in My progress.");
  } catch (e) {
    if (seq === state.openSeq) $("#teach-status").textContent = e.message;
    Yaad?.think(false);
  } finally { if (seq === state.openSeq) $("#teach-go").disabled = false; }
});
$("#teach-again").addEventListener("click", () => {
  $("#teach-result").hidden = true; $("#teach-text").focus(); $("#teach-text").select();
});
$("#teach-listen").addEventListener("click", () => {
  if (!window.speechSynthesis) return;
  claimSpeech("answer"); speechSynthesis.cancel();
  const parts = [$("#teach-verdict").textContent, ...[...document.querySelectorAll("#teach-missed li")].map((li) => `You missed: ${li.textContent}`), $("#teach-q").textContent];
  say(parts.filter(Boolean).join(". "), ++answerSpeech.run, (r) => r === answerSpeech.run, () => releaseSpeech("answer"));
});

// ---------- mock log out (landing.html has the mock log in; no real authentication yet) ----------
$("#logout").addEventListener("click", async () => {
  try { await api("/api/logout", {}); } catch { /* the cookie clears on the next log in either way */ }
  location.replace("/");
});

// ---------- boot ----------
(async () => {
  syncLabels();
  await loadTopics();
  refreshSpend();
  const s = await api("/api/state");
  state.doneDays = s.done_days || [];
  $("#replay-chip").hidden = !s.replay;
  if (s.ask_examples?.length) {   // replay mode: offer the questions Yaad already answered
    $("#ask-try").replaceChildren(el("span", { class: "muted small" }, "Try:"),
      ...s.ask_examples.map((q) => el("button", { class: "chip", type: "button", onclick: () => { $("#ask-q").value = q; askQuestion(q); } }, q)));
    $("#ask-try").hidden = false;
  }
  if (s.plan) {
    const p = s.profile;
    state.topic = p.topic; state.level = s.level; state.formats = p.formats?.length ? p.formats : state.formats;
    // restore the setup form, so going back to Setup shows (and rebuilds) what the learner chose
    $("#goal").value = p.goal || "";
    if (p.deadline) $("#deadline").value = p.deadline;
    $("#minutes").value = p.minutes_per_day || 20;
    $("#depth").value = s.level || p.depth || 2;
    document.querySelectorAll("#formats input").forEach((i) => { i.checked = state.formats.includes(i.value); });
    syncLabels();
    renderPlan(s.plan, p.goal || "");   // draws the plan (and shows it): Today is still the landing page
    pickTopic(p.topic);
  }
  show("setup");
})();


// ---------- add a link or note you found (ingest.py) ----------
let addSeq = 0;
function setAddMode(note) {
  $("#add-link-fields").hidden = note; $("#add-text").hidden = !note;
  $("#add-mode").textContent = note ? "Add a link instead" : "Write a note instead";
  (note ? $("#add-text") : $("#add-url")).focus();
}
function resetAdd() {
  addSeq += 1;                                         // drop a reply that is still on its way
  for (const id of ["#add-url", "#add-note", "#add-text"]) $(id).value = "";
  $("#add-form").hidden = false; $("#add-result").hidden = true; $("#add-status").textContent = ""; $("#add-go").disabled = false;
  $("#add-status").classList.remove("err", "sr-only");
  setAddMode(false);
}
$("#add-open").addEventListener("click", () => { $("#add-dlg").showModal(); resetAdd(); });
$("#add-close").addEventListener("click", () => $("#add-dlg").close());
$("#add-dlg").addEventListener("close", () => { addSeq += 1; Yaad?.think(false); });
$("#add-mode").addEventListener("click", () => setAddMode($("#add-text").hidden));
$("#add-form").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const note = !$("#add-text").hidden;
  const body = note ? { text: $("#add-text").value.trim() } : { url: $("#add-url").value.trim(), note: $("#add-note").value.trim() };
  if (!(note ? body.text : body.url)) { $("#add-status").textContent = note ? "Write a note first." : "Paste a link first."; return; }
  const seq = ++addSeq;
  $("#add-go").disabled = true; Yaad?.think(true); $("#add-status").classList.remove("err");
  $("#add-status").textContent = note ? "Understanding it…" : "Reading the page…";
  try {
    const r = await streamApi("/api/add/stream", body, (t) => { if (seq === addSeq) $("#add-status").textContent = t; });
    if (seq === addSeq) showAdded(r);
    else if (r?.status === "added") reloadBrain();
  } catch (e) {
    if (seq === addSeq) { $("#add-status").textContent = e.message; $("#add-status").classList.add("err"); }
  }
  finally { if (seq === addSeq) { $("#add-go").disabled = false; Yaad?.think(false); } }
});

function showAdded(r) {
  const s = r.save, fresh = r.status === "added";
  const [dot, kind] = Brain.PLATFORM[s.platform] || Brain.PLATFORM.web;
  const close = () => $("#add-dlg").close();
  $("#add-form").hidden = true; $("#add-status").textContent = "";
  $("#add-result").replaceChildren(
    el("div", { class: "add-head" }, icon(fresh ? "check_circle" : "bookmark_added", "good"), el("b", {}, fresh ? "Added to your brain" : "Already in your brain")),
    el("div", { class: "add-card" },
      el("div", { class: "disc-top" }, el("i", { style: `background:${dot}` }), el("span", { class: "muted small" }, kind),
        r.topic ? el("span", { class: "chip on" }, r.topic) : ""),
      el("h3", {}, s.title),
      (r.summary || s.summary) ? el("p", { class: "muted small" }, r.summary || s.summary) : "",
      r.concepts?.length ? el("div", { class: "chips small" }, ...r.concepts.map((c) => el("span", { class: "chip" }, c))) : ""),
    r.learnable === false ? el("p", { class: "muted small" }, "Saved, but there isn't much to learn in it, so it stays out of your topics.") : "",
    el("div", { class: "dlg-actions" },
      el("button", { class: "text-btn", type: "button", onclick: resetAdd }, icon("add"), "Add another"),
      r.topic ? el("button", { class: "tonal", type: "button", onclick: () => { close(); showInBrain(r.topic, s.id); } }, icon("bubble_chart"), "See it in my brain") : "",
      r.learnable !== false ? el("button", { class: "primary", type: "button", onclick: () => { close(); openMini(s.id); } }, icon("school"), "Teach me this") : ""));
  $("#add-result").hidden = false;
  $("#add-status").textContent = fresh ? `Added to your brain${r.topic ? `, under ${r.topic}` : ""}.` : "Already in your brain.";
  $("#add-status").classList.add("sr-only");
  ($("#add-result .dlg-actions .primary") || $("#add-result .dlg-actions button")).focus();
  if (fresh) {
    Yaad?.mood("happy");
    Yaad?.say(r.topic ? `Got it! Filed under ${r.topic}.` : "Got it, saved!", 4500);
    reloadBrain();
    loadToday();
  }
}

function reloadBrain() {                               // redraw the brain (and the topic list) with the new save
  brain?.pause();
  brain = null; brainLoading = null;
  loadTopics({ initial: false }).catch(() => {});
  ensureBrain();                                       // now if Learn is showing; otherwise the next time it is
}
async function showInBrain(topic, id) {
  show("setup");
  await brainLoading;
  brain?.select(topic, { zoomIn: true });
  setTimeout(() => brain?.highlight(id, true), 900);
  $("#brain").scrollIntoView({ block: "center", behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth" });
}


// ---------- Today: your backlog in numbers and today's lesson, picked for you (today.py) ----------
let todayMore = 0, todayData = null, todaySeq = 0;
async function loadToday() {
  const seq = ++todaySeq;
  let d;
  try { d = await api("/api/today", { more: todayMore }); }
  catch {
    if (seq === todaySeq && !todayData) { $("#today-line").textContent = "Couldn't load today's pick. Is the server running?"; $("#today-start").hidden = true; }
    return;
  }
  if (seq !== todaySeq) return;
  todayData = d;
  const b = d.backlog;
  for (const k of ["saved", "learned", "waiting", "fading", "gone"]) $(`#bl-${k}`).textContent = b[k];
  $("#backlog").hidden = !b.saved;
  $("#backlog-sub").textContent = b.saved
    ? `Waiting: ${b.quick} quick (5 min or less) · ${b.deep} deep reads (20 min+) · ${b.not_lessons} aren't lessons (tools, profiles) · Yaad already read ${b.read_for_you.slides} slides and ${b.read_for_you.reels} Reels for you`
    : "";
  renderToday(d);
}

function describePick(cards) {   // "A 25-min deep read (Instagram post) + a 5-min Reel"
  const kind = (c) => ({ "Your note": "note", "Web page": "article" }[(Brain.PLATFORM[c.platform] || Brain.PLATFORM.web)[1]]
    || (Brain.PLATFORM[c.platform] || Brain.PLATFORM.web)[1]);
  const text = cards.map((c) => (c.minutes > 60 ? `the key ideas of a ${Math.round(c.minutes / 60)}-hour ${kind(c)}`
    : c.minutes >= 20 ? `a ${c.minutes}-min deep read (${kind(c)})` : `a ${c.minutes}-min ${kind(c)}`)).join(" + ");
  return text.charAt(0).toUpperCase() + text.slice(1);
}

function renderToday(d) {
  const b = d.backlog, t = d.today;
  const start = $("#today-start"), more = $("#today-more"), review = $("#today-review");
  review.hidden = !b.fading;
  if (b.fading) setBtn(review, "replay", `${b.fading} fading · 2-min review`);
  more.hidden = true; start.hidden = false;
  $("#today-meta").textContent = "";
  if (!b.saved) {                                        // first run: nothing saved yet
    $("#today-title").textContent = "Your brain is empty";
    $("#today-list").replaceChildren(el("li", { class: "today-empty" }, "Share links to Yaadsi on WhatsApp (coming soon) or paste one here. Yaad reads it and teaches it to you."));
    $("#today-line").textContent = "";
    setBtn(start, "add_link", "Add a link");
    setYaadLine("Hi, I'm Yaad! Send me something you saved and I'll teach it to you.");
    return;
  }
  if (!t.save_ids.length) {                              // nothing left to learn
    $("#today-title").textContent = "Backlog cleared 🎉";
    $("#today-list").replaceChildren(el("li", { class: "today-empty" }, "Nothing is waiting. Save something new, or keep what you learned fresh."));
    $("#today-line").textContent = ""; start.hidden = true;
    setYaadLine("You cleared your backlog! Keep saving and I'll keep teaching.");
    return;
  }
  $("#today-title").textContent = t.done ? (t.index ? "One more done 🎉" : "Done for today 🎉") : `Today · about ${t.taught_minutes} min`;
  $("#today-meta").textContent = t.topic;
  $("#today-list").replaceChildren(...t.cards.map((c) => {
    const [dot, kind] = Brain.PLATFORM[c.platform] || Brain.PLATFORM.web;
    return el("li", { class: t.done ? "done" : "" },
      el("i", { class: "dot", style: `background:${dot}`, title: kind }),
      el("span", { class: "t-title" }, c.title), timeChip(c.minutes), keptBadge(c),
      c.url ? el("a", { class: "t-open", href: c.url, target: "_blank", rel: "noopener noreferrer",
        title: `Open the original on ${kind}`, "aria-label": `Open the original: ${c.title}` }, icon("open_in_new")) : "");
  }));
  $("#today-line").textContent = t.done ? "See you tomorrow, or do one more now."
    : `${describePick(t.cards)}, taught in about ${t.taught_minutes} minutes.`;
  start.hidden = t.done; more.hidden = !t.done || !b.waiting;
  setBtn(start, "play_arrow", "Start");
  setYaadLine(t.done ? "That's today's done. See you tomorrow!" : `${b.waiting} things are waiting. Today's pick takes about ${t.taught_minutes} minutes.`);
}

function setYaadLine(line) {                             // Yaad's resting line on Today (only while it sits in the hero)
  YAAD_LINES.setup = line;
  if (document.body.classList.contains("view-setup") && heroInView) Yaad?.setRest(line);   // never cut off a line being said
}

$("#today-start").addEventListener("click", () => {
  if (!todayData?.backlog.saved) { $("#add-open").click(); return; }
  if (todayData.today.save_ids.length) openMini(todayData.today.save_ids, { from: "today" });
});
$("#today-more").addEventListener("click", () => { todayMore += 1; loadToday(); });
$("#today-review").addEventListener("click", () => { show("progress"); setTimeout(() => window.Progress?.startReview(), 600); });
document.querySelectorAll("#backlog .bl").forEach((b) => b.addEventListener("click", () => {
  const k = b.dataset.bl;
  if (k === "gone") openGone();
  else if (k === "fading" && todayData?.backlog.fading) $("#today-review").click();
  else if (k === "learned") show("progress");
  else $("#today-card").scrollIntoView({ block: "center", behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth" });
}));

function openGone() {
  const items = todayData?.backlog.gone_items || [];
  $("#gone-list").replaceChildren(...items.map((g) => {
    const [dot, kind] = Brain.PLATFORM[g.platform] || Brain.PLATFORM.web;
    return el("li", {}, el("i", { class: "dot", style: `background:${dot}` }), el("span", {}, kind),
      g.url ? el("a", { href: g.url, target: "_blank", rel: "noopener noreferrer", class: "small" }, "link") : "",
      g.note ? el("span", { class: "muted small" }, `your note: ${g.note}`) : "");
  }));
  $("#gone-dlg").showModal();
}
$("#gone-close").addEventListener("click", () => $("#gone-dlg").close());

function openCustomise() {                               // the optional topic plan (goal, deadline, depth)
  if ($("#customise").open) return;
  $("#customise").open = true;
  setTimeout(() => $("#customise").scrollIntoView({ block: "start", behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth" }), 50);
}

// "More ways to learn" only appears when it has something inside
const MORE_WAYS = ["#diagram-box", "#extra-box", "#teach-card"];
function syncMoreWays() {
  const empty = MORE_WAYS.every((s) => $(s).hidden);
  if ($("#more-ways").hidden !== empty) $("#more-ways").hidden = empty;   // only write on a real change
}
// watch only the three cards (watching #more-ways itself would observe its own change and loop forever)
const moreWaysWatch = new MutationObserver(syncMoreWays);
MORE_WAYS.forEach((s) => moreWaysWatch.observe($(s), { attributes: true, attributeFilter: ["hidden"] }));
