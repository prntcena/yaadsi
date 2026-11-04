// The landing page for logged-out visitors: the pitch, plus the mock login (admin / admin; no real authentication yet).
(function () {
  const $ = (s) => document.querySelector(s);
  const dlg = $("#login-dlg"), err = $("#login-err"), go = $("#login-go");

  // Yaad waves from the hero (decorative: the line beside it is plain text)
  if (window.Yaad) { Yaad.mount($("#lp-yaad")); Yaad.mood("happy", 2400); }

  if (document.fonts?.ready) {   // icon font blocked: hide the ligature words instead of showing "play_arrow"
    document.fonts.ready.then(() => { if (!document.fonts.check('20px "Material Symbols Rounded"')) document.body.classList.add("no-icons"); });
  }

  async function login(username, password) {
    const res = await fetch("/api/login", { method: "POST", headers: { "Content-Type": "application/json", "X-Yaadsi": "1" },
      body: JSON.stringify({ username, password }) });
    if (!res.ok) {
      const data = await res.json().catch(() => ({}));
      throw new Error(typeof data.detail === "string" ? data.detail : "Couldn't log in. Try again.");
    }
    location.replace("/");   // the server now serves the app
  }

  document.querySelectorAll("[data-demo]").forEach((b) => b.addEventListener("click", async () => {
    b.disabled = true;
    try { await login("admin", "admin"); }
    catch (e) { b.disabled = false; openLogin(); err.textContent = e.message; }
  }));

  function openLogin() {
    err.textContent = "";
    if (!dlg.open) dlg.showModal();
    $("#login-user").focus();
  }
  document.querySelectorAll("[data-login]").forEach((b) => b.addEventListener("click", openLogin));
  dlg.querySelector("[data-close]").addEventListener("click", () => dlg.close());
  dlg.addEventListener("click", (e) => { if (e.target === dlg) dlg.close(); });   // click on the backdrop

  $("#login-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const user = $("#login-user").value.trim(), pass = $("#login-pass").value;
    if (!user || !pass) { err.textContent = "Enter a username and password."; return; }
    go.disabled = true; err.textContent = "";
    try { await login(user, pass); }
    catch (ex) { err.textContent = ex.message; $("#login-pass").select(); }
    finally { go.disabled = false; }
  });
})();
