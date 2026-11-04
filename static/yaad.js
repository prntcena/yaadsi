// Yaad: the tutor's buddy, a little memory blob. One inline SVG; states are CSS classes:
// idle (bob + blink), thinking, talking, happy, curious, learned (sprout glows). No model calls.
(function () {
  const SVG = `
<svg class="yaad-svg" viewBox="0 0 140 140" aria-hidden="true">
  <defs>
    <radialGradient id="yaad-body" cx="40%" cy="35%" r="70%">
      <stop offset="0%" stop-color="var(--yaad-light)"/>
      <stop offset="100%" stop-color="var(--yaad-main)"/>
    </radialGradient>
    <radialGradient id="yaad-glow" cx="50%" cy="50%" r="50%">
      <stop offset="0%" stop-color="#ffe66d" stop-opacity=".95"/>
      <stop offset="100%" stop-color="#ffe66d" stop-opacity="0"/>
    </radialGradient>
  </defs>
  <g class="yaad-float">
    <g class="yaad-sprout">
      <circle class="yaad-glow" cx="70" cy="22" r="20" fill="url(#yaad-glow)"/>
      <path d="M70 40 C70 32 70 28 70 22" stroke="#3f9b5a" stroke-width="3.5" stroke-linecap="round" fill="none"/>
      <path d="M70 28 C62 18 52 20 50 24 C56 30 64 31 70 28Z" fill="#5cc27a"/>
      <path d="M70 24 C77 13 88 14 90 18 C85 25 77 27 70 24Z" fill="#6fd38b"/>
    </g>
    <path class="yaad-body" fill="url(#yaad-body)" d="M70 38
      C84 34 98 40 104 50 C116 54 122 66 118 78 C124 90 116 106 102 110
      C96 122 80 126 70 120 C60 126 44 122 38 110 C24 106 16 90 22 78
      C18 66 24 54 36 50 C42 40 56 34 70 38Z"/>
    <path class="yaad-fold" d="M52 56 C58 62 58 70 52 76 M88 56 C82 62 82 70 88 76" stroke="rgba(255,255,255,.28)"
          stroke-width="3" fill="none" stroke-linecap="round"/>
    <ellipse class="yaad-cheek" cx="44" cy="92" rx="8" ry="5" fill="#ff8fa3" opacity=".55"/>
    <ellipse class="yaad-cheek" cx="96" cy="92" rx="8" ry="5" fill="#ff8fa3" opacity=".55"/>
    <g class="yaad-eyes">
      <g class="yaad-eye"><ellipse cx="55" cy="80" rx="8" ry="10" fill="#1d2030"/><circle cx="58" cy="76" r="3" fill="#fff"/></g>
      <g class="yaad-eye"><ellipse cx="85" cy="80" rx="8" ry="10" fill="#1d2030"/><circle cx="88" cy="76" r="3" fill="#fff"/></g>
    </g>
    <path class="yaad-smile" d="M60 98 Q70 107 80 98" stroke="#1d2030" stroke-width="3.5" fill="none" stroke-linecap="round"/>
    <ellipse class="yaad-mouth" cx="70" cy="101" rx="6" ry="5" fill="#1d2030"/>
    <g class="yaad-dots"><circle cx="112" cy="40" r="4"/><circle cx="122" cy="30" r="5"/><circle cx="132" cy="18" r="6"/></g>
    <g class="yaad-sparkles"><path d="M20 40 l3 8 8 3 -8 3 -3 8 -3 -8 -8 -3 8 -3z"/><path d="M118 112 l2 6 6 2 -6 2 -2 6 -2 -6 -6 -2 6 -2z"/></g>
  </g>
</svg>`;

  const root = document.createElement("div");
  root.className = "yaad idle";
  root.innerHTML = SVG + '<div class="yaad-bubble" role="status" aria-live="polite" hidden></div>';   // static markup only
  const bubble = root.querySelector(".yaad-bubble");
  let stateTimer = null, bubbleTimer = null, blinkTimer = null, talking = false, resting = null, saying = false;

  function blinkLoop() {
    clearTimeout(blinkTimer);
    blinkTimer = setTimeout(() => {
      root.classList.add("blink");
      setTimeout(() => root.classList.remove("blink"), 160);
      blinkLoop();
    }, 3000 + Math.random() * 3000);
  }

  const MOODS = ["thinking", "happy", "curious", "learned", "sad"];
  const Yaad = {
    el: root,
    mount(container) { if (container && root.parentElement !== container) container.append(root); },
    mood(name, ms = 2400) {                       // a temporary mood on top of idle
      clearTimeout(stateTimer);
      MOODS.forEach((m) => root.classList.remove(m));
      if (name) root.classList.add(name);
      if (name && ms) stateTimer = setTimeout(() => root.classList.remove(name), ms);
    },
    think(on) { if (on) this.mood("thinking", 0); else root.classList.remove("thinking"); },   // never clears a cheer
    talk(on) { talking = on; root.classList.toggle("talking", on); },
    rest(text) {                                  // the line to come back to after a timed one (null = none)
      resting = text || null;
      this.say(resting, 0);
    },
    say(text, ms = 4200) {
      clearTimeout(bubbleTimer);
      bubble.textContent = text || "";
      bubble.hidden = !text;
      saying = !!(text && ms);
      if (saying) bubbleTimer = setTimeout(() => { saying = false; bubble.textContent = resting || ""; bubble.hidden = !resting; }, ms);
    },
    setRest(text) {                               // change the resting line; a timed line keeps playing first
      resting = text || null;
      if (!saying) this.say(resting, 0);
    },
  };
  blinkLoop();
  window.Yaad = Yaad;
})();
