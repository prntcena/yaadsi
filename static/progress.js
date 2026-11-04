// "My progress": an honest dashboard built on spaced reviews. Every state comes with an action.
(function () {
  const $ = (sel) => document.querySelector(sel);
  const STATE = {
    learning: { label: "Learning", icon: "spa" }, review: { label: "To review", icon: "replay" },
    mastered: { label: "Mastered", icon: "emoji_events" }, new: { label: "Not started", icon: "radio_button_unchecked" },
  };
  const demo = new URLSearchParams(location.search).get("demo") === "1";
  let data = null;

  function meter(box) {
    return el("span", { class: "meter", "aria-label": `${box} of 4 reviews` },
      ...[1, 2, 3, 4].map((i) => el("i", { class: i <= box ? "on" : "" })));
  }
  function item(s) {
    const [dot] = (window.Brain?.PLATFORM || {})[s.platform] || ["#9aa1ad"];
    const when = s.state === "review" ? "due now" : s.state === "mastered" ? `check again in ${s.due_in_days}d` : `next review in ${s.due_in_days}d`;
    return el("button", { class: `pitem ${s.state}`, type: "button", title: s.title, onclick: () => openMini(s.id) },
      el("i", { class: "dot", style: `background:${dot}` }),
      el("span", { class: "ptitle" }, s.title), el("span", { class: "pmeta muted small" }, meter(s.box), ` ${when}`, s.minutes ? ` · ${s.minutes} min` : ""));
  }

  async function load() {
    data = await api("/api/progress");
    const st = data.stats;
    $("#prog-stats").replaceChildren(...["learning", "review", "mastered"].map((k) =>
      el("div", { class: `stat ${k}` }, el("b", {}, String(st[k])), el("span", {}, icon(STATE[k].icon), STATE[k].label))));
    $("#prog-sub").textContent = `${st.minutes_learned} min learned · ${st.days_done} day${st.days_done === 1 ? "" : "s"} done · ${st.new} saves not started yet`;
    // Review now
    $("#review-card").hidden = !st.review;
    const due = data.saves.filter((s) => s.state === "review");
    $("#review-list").replaceChildren(...due.slice(0, 4).map((s) => el("li", {}, s.title)), ...(due.length > 4 ? [el("li", { class: "muted" }, `+${due.length - 4} more`)] : []));
    // Columns
    for (const k of ["learning", "review", "mastered"]) {
      const list = data.saves.filter((s) => s.state === k);
      $(`#col-${k}`).replaceChildren(...(list.length ? list.map(item) :
        [el("p", { class: "muted small" }, k === "mastered" ? "Answer a review right on 3 different days and it lands here." :
          k === "review" ? "Nothing fading right now. 🎉" : "Finish a lesson's quiz to start remembering it.")]));
    }
    // Topics
    $("#prog-topics").replaceChildren(...data.topics.map((t) => {
      const n = Math.max(1, t.total);
      const bar = el("div", { class: "tbar", role: "img", "aria-label": `${t.mastered} mastered, ${t.learning} learning, ${t.review} to review, ${t.new} not started` },
        ...["mastered", "learning", "review", "new"].map((k) => (t[k] ? el("span", { class: k, style: `width:${(100 * t[k]) / n}%` }) : "")));
      const action = t.review ? ["Review", () => startReview()] : t.status === "Not started" ? ["Start", () => openTopic(t.name)] : ["Continue", () => show("plan")];
      return el("div", { class: "trow" }, el("div", { class: "tname" }, el("b", {}, t.name), el("span", { class: `tstatus ${t.status.replace(/ /g, "-").toLowerCase()}` }, t.status)),
        bar, el("div", { class: "tacts" }, el("button", { class: "chip", type: "button", onclick: action[1] }, action[0]),
          el("button", { class: "chip dots-btn", type: "button", title: "Big ideas, links, gaps and an order to learn them",
            onclick: () => window.Masterclass?.open(t.name, "progress") }, icon("hub"), "Connect the dots")));
    }));
    $("#demo-bar").hidden = !(demo && data.demo);
    $("#demo-day").textContent = data.demo_offset_days ? `Demo clock: +${data.demo_offset_days} days (${data.today})` : "";
    Yaad?.say(st.review ? `${st.review} thing${st.review > 1 ? "s are" : " is"} fading. A 2-minute review?` :
      st.learning || st.mastered ? "Nothing due. Your memory is in good shape!" : "Finish a lesson quiz and I'll start tracking what sticks.", 5000);
    return data;
  }

  function openTopic(name) {
    show("setup");
    setTimeout(() => window.__brainSelect?.(name), 400);
  }

  // ---- 2-minute review (questions come from your own cached lessons; no model calls)
  const rv = { qs: [], i: 0, right: 0, before: null };
  async function startReview() {
    const { questions } = await api("/api/review?limit=5");
    if (!questions.length) { Yaad?.say("Nothing to review right now!", 3000); return; }
    Object.assign(rv, { qs: questions, i: 0, right: 0, before: data?.stats });
    $("#review-card").hidden = false;
    $("#review-intro").hidden = true; $("#review-run").hidden = false;
    $("#review-run").scrollIntoView({ block: "center", behavior: "smooth" });
    askNext();
  }
  function askNext() {
    const q = rv.qs[rv.i];
    if (!q) return finishReview();
    const why = el("div", { class: "why" });
    const next = el("button", { class: "primary", type: "button", hidden: "", onclick: () => { rv.i += 1; askNext(); } }, rv.i + 1 < rv.qs.length ? "Next →" : "See results");
    const opts = q.options.map((o, i) => el("button", { class: "opt", type: "button", onclick: (ev) => {
      opts.forEach((b, j) => { b.disabled = true; if (j === q.answer_index) b.classList.add("right"); });
      const ok = i === q.answer_index;
      if (!ok) ev.currentTarget.classList.add("wrong"); else rv.right += 1;
      why.textContent = (ok ? "Right! " : "Not quite. ") + (q.why || "");
      api("/api/feedback", { kind: ok ? "quiz_right" : "quiz_wrong", lesson_id: q.lesson_id, detail: `review: ${q.q}` }).catch(() => {});
      if (ok) Yaad?.mood("happy", 1500);
      next.hidden = false; next.focus();
    } }, o));
    $("#review-run").replaceChildren(el("div", { class: "muted small" }, `Question ${rv.i + 1} of ${rv.qs.length} · from "${q.about}"`),
      el("div", { class: "q" }, el("p", {}, q.q), ...opts, why), next);
  }
  async function finishReview() {
    await new Promise((r) => setTimeout(r, 400));             // let the last answer reach the server
    const after = (await load()).stats;
    const moved = Math.max(0, (rv.before?.review || 0) - after.review);
    $("#review-run").replaceChildren(el("p", {}, el("b", {}, `${rv.right}/${rv.qs.length} right.`),
      moved ? ` ${moved} save${moved > 1 ? "s" : ""} no longer fading.` : " Reviews move a save up only on a new day, so come back tomorrow!"),
      el("button", { class: "chip", type: "button", onclick: () => { $("#review-run").hidden = true; $("#review-intro").hidden = false; } }, "Done"));
    $("#review-card").hidden = false;
    Yaad?.mood("happy"); Yaad?.say(`${rv.right}/${rv.qs.length}! Spaced reviews are how things stick.`, 4500);
  }

  $("#review-start").addEventListener("click", startReview);
  $("#demo-advance").addEventListener("click", async () => {
    await api("/api/demo/advance", { days: 3 });
    load();
    Yaad?.say("⏩ Three days later… some things are fading!", 3500);
  });
  window.Progress = { load, startReview };
})();
