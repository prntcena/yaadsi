"""Yaadsi Tutor web app: local, or the keyless replay demo on Vercel (see HOSTED below).

    python -m uvicorn app:app --port 8765
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from typing import Literal

from fastapi import BackgroundTasks, FastAPI, File, HTTPException, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware
from pydantic import BaseModel, Field

HOSTED = bool(os.environ.get("VERCEL"))   # the hosted replay demo: set by Vercel itself, before the modules below read it
if HOSTED:   # the public corpus, no background builds, and a throwaway learner in the only writable folder
    os.environ.setdefault("TUTOR_DATA", "demo")
    os.environ.setdefault("TUTOR_PREWARM", "0")
    os.environ.setdefault("TUTOR_PROFILE", "/tmp/yaadsi-profile.json")

import ask  # noqa: E402  (after the hosted defaults)
import eval as evaluation
import improve
import ingest
import progress
import teachback
import thoughts
import today
import llm
import masterclass
import planner
import prewarm
import profile as learner
import tools
import visuals
from lesson import cache_key, is_cached, make_lesson

if HOSTED and llm.has_keys():   # the login is a published mock, so a hosted key would be anyone's to spend
    raise RuntimeError("The hosted demo runs in replay mode: remove the API keys from the Vercel project.")

STATIC = llm.HERE / "static"
app = FastAPI(title="Yaadsi Tutor")
# Local-only app: reject other Host headers so a DNS-rebinding page cannot drive the paid endpoints.
# The hosted demo also answers on Vercel's domain; it runs with no keys, so nothing there is paid.
app.add_middleware(TrustedHostMiddleware,
                   allowed_hosts=["127.0.0.1", "localhost"] + (["*.vercel.app"] if HOSTED else []))
app.mount("/static", StaticFiles(directory=STATIC), name="static")

UPLOAD_LIMIT = 5 * 1024 * 1024 + 64 * 1024   # the 5 MB recording plus multipart overhead


REPLAY_MSG = llm.REPLAY_MSG


def _model_error(exc: Exception, message: str) -> HTTPException:
    """A missing key is the judge's setup, not a failure: say so instead of \"try again\"."""
    return HTTPException(400, REPLAY_MSG) if isinstance(exc, llm.NoKey) else HTTPException(502, message)


@app.middleware("http")
async def guard_posts(request: Request, call_next):
    """Other web pages can send simple cross-site POSTs (forms, multipart) to localhost without a preflight.
    Requiring a custom header forces a preflight that fails, so only this page can trigger paid calls.
    Oversized uploads are refused from the header, before Starlette spools the body to disk."""
    if request.method == "POST":
        if request.headers.get("x-yaadsi") != "1":
            return JSONResponse({"detail": "Forbidden."}, status_code=403)
        if request.url.path == "/api/transcribe":
            length = request.headers.get("content-length")
            if not length or not length.isdigit() or int(length) > UPLOAD_LIMIT:
                return JSONResponse({"detail": "That recording is too long."}, status_code=413)
    response = await call_next(request)
    if request.url.path == "/" or request.url.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-cache"   # revalidate by ETag: never run yesterday's scripts
    return response


class PlanIn(BaseModel):
    topic: str
    deadline: str
    minutes_per_day: int = Field(ge=5, le=120)
    goal: str = Field(default="", max_length=200)
    order: list[str] | None = Field(default=None, max_length=40)   # a learning order from Connect the dots


class StartIn(PlanIn):
    depth: int = Field(ge=1, le=5)
    formats: list[str] = []


class LessonIn(BaseModel):
    day: int | None = Field(default=None, ge=1)
    save_ids: list[str] | None = Field(default=None, max_length=3)   # a "mini lesson" outside the plan
    level: int | None = Field(default=None, ge=1, le=5)
    fresh: bool = False
    best_of: int = Field(default=3, ge=1, le=3)   # drafts judged by Muse Wonder (improve.py)


class FeedbackIn(BaseModel):
    kind: str = Field(pattern="^(too_hard|too_easy|just_right|thumbs_up|thumbs_down|quiz_right|quiz_wrong)$")
    lesson_id: str = Field(pattern=r"^[0-9a-f]{16}$")
    section: str = Field(default="", max_length=120)
    detail: str = Field(default="", max_length=200)


# MOCK AUTH: not security. One fixed demo account and a fixed cookie value, only so logged-out visitors see the landing
# page first. The /api/* endpoints are not gated. Real accounts come later (the product's Slice 5 design: sessions, lockout).
MOCK_USER, MOCK_PASSWORD = "admin", "admin"
SESSION_COOKIE, SESSION_VALUE = "yaadsi_session", "mock"


def _signed_in(request: Request) -> bool:
    return request.cookies.get(SESSION_COOKIE) == SESSION_VALUE


@app.get("/")
def index(request: Request) -> FileResponse:
    """Logged out: the landing page (what Yaadsi is). Logged in: the app."""
    return FileResponse(STATIC / ("index.html" if _signed_in(request) else "landing.html"))


class LoginIn(BaseModel):
    username: str = Field(max_length=100)
    password: str = Field(max_length=200)


@app.post("/api/login")
def login(body: LoginIn) -> JSONResponse:
    if (body.username.strip(), body.password) != (MOCK_USER, MOCK_PASSWORD):
        raise HTTPException(401, "Wrong username or password.")
    response = JSONResponse({"ok": True})
    response.set_cookie(SESSION_COOKIE, SESSION_VALUE, max_age=30 * 24 * 3600, httponly=True, samesite="lax", path="/")
    return response


@app.post("/api/logout")
def logout() -> JSONResponse:
    response = JSONResponse({"ok": True})
    response.delete_cookie(SESSION_COOKIE, path="/")
    return response


@app.get("/api/topics")
def topics() -> dict:
    return {"topics": tools.topics_overview(), "today": date.today().isoformat()}


@app.post("/api/plan")
def plan_preview(body: PlanIn) -> dict:
    result = planner.plan(body.topic, body.deadline, body.minutes_per_day, body.goal, order=body.order)
    if "error" in result:
        raise HTTPException(400, result["error"])
    return result


@app.post("/api/start")
def start(body: StartIn) -> dict:
    result = planner.plan(body.topic, body.deadline, body.minutes_per_day, body.goal, order=body.order)
    if "error" in result:
        raise HTTPException(400, result["error"])
    with learner.editing() as p:
        p.update(goal=body.goal, topic=body.topic, deadline=body.deadline, minutes_per_day=body.minutes_per_day,
                 depth=body.depth, formats=body.formats, plan=result, plan_started=time.strftime("%Y-%m-%dT%H:%M:%S"))
        p["levels"][body.topic] = body.depth
    prewarm.schedule()   # build every day's lesson in the background so opening a day is instant
    return {"plan": result, "level": body.depth}


@app.on_event("startup")
def prewarm_current_plan() -> None:
    p = learner.load()
    prewarm.schedule(set(done_days(p)))


@app.get("/api/prewarm")
def prewarm_status() -> dict:
    return prewarm.status()


@app.get("/api/state")
def state() -> dict:
    p = learner.load()
    return {"profile": {k: p[k] for k in ("goal", "topic", "deadline", "minutes_per_day", "depth", "formats")},
            "plan": p.get("plan"), "level": learner.level(p, p["topic"]) if p["topic"] else None,
            "done_days": done_days(p), "replay": (replay := not llm.has_keys()), "formats_chosen": bool(p.get("formats_chosen")),
            "ask_examples": ask.examples(learner.level(p, p["topic"]) if p["topic"] else 2) if replay else []}


@app.post("/api/lesson")
def lesson(body: LessonIn) -> dict:
    p = learner.load()
    plan = p.get("plan")
    topic = p["topic"]
    if body.save_ids:
        held = evaluation.held_out_ids()
        if not all(s in tools.saves() and s not in held for s in body.save_ids):
            raise HTTPException(400, "Unknown save.")
        spec = today.mini_spec(body.save_ids, p)   # its own save's topic; the same key pre-warm uses
        day, topic = {"saves": spec["save_ids"], "minutes": spec["minutes"]}, spec["topic"]
    else:
        if not plan or not body.day or body.day > len(plan["days"]):
            raise HTTPException(400, "Start a plan first.")
        day = plan["days"][body.day - 1]
    level = body.level or learner.level(p, topic)
    if not llm.has_keys() and (body.fresh or not is_cached(cache_key(day["saves"], level, p.get("style_rules") or [], topic,
                                                                       day["minutes"], body.best_of))):
        raise HTTPException(400, REPLAY_MSG)
    try:
        result = make_lesson(day["saves"], level, topic, day["minutes"], p.get("style_rules"),
                             use_cache=not body.fresh, best_of=body.best_of)
    except llm.LLMError as exc:
        raise _model_error(exc, f"The tutor could not build this lesson ({exc}). Try again.") from None
    except (TypeError, AttributeError, KeyError, ValueError):   # malformed model output that slipped past validation
        raise HTTPException(502, "The tutor wrote a lesson it could not use. Try again.") from None
    return {**result, "day": body.day, "mini": bool(body.save_ids),
            "source_cards": [tools.card(s) for s in result["sources"]]}


@app.get("/api/related")
def related(lesson_id: str) -> dict:
    if not re.fullmatch(r"[0-9a-f]{16}", lesson_id):
        raise HTTPException(400, "Bad lesson id.")
    path = llm.DATA / "lessons" / f"{lesson_id}.json"
    if not path.exists():
        raise HTTPException(404, "Lesson not found.")
    l = json.loads(path.read_text(encoding="utf-8"))
    p = learner.load()
    plan_days = (p.get("plan") or {}).get("days", [])
    done = set(done_days(p))
    seen = {s for d in plan_days if d["day"] in done for s in d["saves"]}
    planned = {s for d in plan_days for s in d["saves"]}
    return {"related": tools.related(l.get("save_ids", []), evaluation.held_out_ids() | seen, p.get("goal", ""), planned=planned)}


@app.post("/api/feedback")
def feedback(body: FeedbackIn, background: BackgroundTasks) -> dict:
    path = llm.DATA / "lessons" / f"{body.lesson_id}.json"
    if not path.exists():
        raise HTTPException(404, "Lesson not found.")
    topic = json.loads(path.read_text(encoding="utf-8")).get("topic") or ""   # Today's lessons need no plan
    with learner.editing() as p:
        new_level = learner.apply_feedback(p, body.kind, body.lesson_id, topic or p["topic"], body.section, body.detail)
        due = len(p["history"]) - p.get("rules_at", 0) >= improve.RULES_EVERY
    if due:
        background.add_task(_learn_rules)
    if body.kind in ("too_hard", "too_easy"):   # the level moved: pre-build the lessons at the new level
        background.add_task(prewarm.schedule, set(done_days(p)))
    return {"level": new_level, "learning": due}


_learning = threading.Lock()   # one rule-learning call at a time; feedback during it waits for the next round


def _learn_rules() -> None:
    """Muse Wonder rewrites the style rules from the feedback log (runs after the response is sent)."""
    if not _learning.acquire(blocking=False):
        return
    try:
        snapshot = learner.load()
        if not improve.maybe_learn_rules(snapshot):
            return
        with learner.editing() as p:   # merge only the learned fields; feedback may have arrived meanwhile
            if snapshot["rules_at"] <= p.get("rules_at", 0):
                return                 # never move backwards to rules learned from less evidence
            for key in ("style_rules", "learner_summary", "rules_at"):
                p[key] = snapshot[key]
            p.setdefault("rules_history", []).append(snapshot["rules_history"][-1])
        prewarm.schedule(set(done_days(p)))   # new rules change every lesson's cache key
    finally:
        _learning.release()


@app.get("/api/learner")
def learner_view() -> dict:
    p = learner.load()
    last = (p.get("rules_history") or [{}])[-1]
    counts: dict[str, int] = {}
    for ev in p.get("history", []):
        counts[ev["kind"]] = counts.get(ev["kind"], 0) + 1
    return {"levels": p.get("levels", {}), "rules": p.get("style_rules", []), "new_rules": last.get("new", []),
            "summary": p.get("learner_summary", ""), "feedback": len(p.get("history", [])), "counts": counts,
            "next_update_in": max(0, improve.RULES_EVERY - (len(p.get("history", [])) - p.get("rules_at", 0))),
            "updates": len(p.get("rules_history", []))}


FORMATS = ("diagram", "quiz", "figure", "picture", "animation", "audio")   # the lesson formats the page can show
Format = Literal["diagram", "quiz", "figure", "picture", "animation", "audio"]


class VisualsIn(BaseModel):
    lesson_id: str = Field(pattern=r"^[0-9a-f]{16}$")
    formats: list[Format] | None = Field(default=None, max_length=len(FORMATS))   # what the page shows right now


class PrefsIn(BaseModel):
    formats: list[Format] = Field(max_length=len(FORMATS))


@app.post("/api/prefs")
def save_prefs(body: PrefsIn) -> dict:
    """The formats chosen at the end of a lesson ("Make it your way") become the learner's default."""
    with learner.editing() as p:
        p["formats"] = [f for f in FORMATS if f in body.formats]
        p["formats_chosen"] = True
        return {"formats": p["formats"]}


def _safe_visual(fn, *args) -> tuple[dict | None, str]:
    try:
        return fn(*args), ""
    except (llm.LLMError, OSError, ValueError) as exc:
        return None, str(exc)[:200]


@app.post("/api/visuals")
def lesson_visuals(body: VisualsIn) -> dict:
    path = llm.DATA / "lessons" / f"{body.lesson_id}.json"
    if not path.exists():
        raise HTTPException(404, "Lesson not found.")
    lesson_data = json.loads(path.read_text(encoding="utf-8"))
    formats = set(body.formats if body.formats is not None else learner.load().get("formats") or [])
    jobs = {}
    with ThreadPoolExecutor(2) as pool:
        if "figure" in formats:      # the real figure from the save (SAM crop)
            jobs["figure"] = pool.submit(_safe_visual, visuals.figure_for, lesson_data["save_ids"])
        if "picture" in formats:     # the kid-simple drawing (Muse Image)
            jobs["picture"] = pool.submit(_safe_visual, visuals.picture_for, lesson_data)
        results = {name: job.result() for name, job in jobs.items()}
    return {name: value for name, (value, _) in results.items()} | \
           {"errors": {name: err for name, (_, err) in results.items() if err}}


@app.get("/media/{name}")
def media(name: str) -> FileResponse:
    if visuals.MEDIA_NAME.fullmatch(name) and (visuals.MEDIA / name).is_file():
        return FileResponse(visuals.MEDIA / name, media_type="image/png")
    path = visuals.full_slide_path(name)
    if path is None:
        raise HTTPException(404, "Not found.")
    return FileResponse(path)


MAX_AUDIO_BYTES = 5 * 1024 * 1024
MAX_AUDIO_SECONDS = 60


def wav_problem(data: bytes) -> str:
    """Muse Voice Transcribe takes mono 16-bit PCM WAV at 16 or 24 kHz; check the real header, not the label."""
    if len(data) < 44 or data[:4] != b"RIFF" or data[8:12] != b"WAVE" or data[12:16] != b"fmt ":
        return "Please send a WAV recording."
    fmt, channels, rate, bits = (int.from_bytes(data[20:22], "little"), int.from_bytes(data[22:24], "little"),
                                 int.from_bytes(data[24:28], "little"), int.from_bytes(data[34:36], "little"))
    if fmt != 1 or channels != 1 or bits != 16 or rate not in (16000, 24000):
        return "The recording must be mono 16-bit audio at 16 or 24 kHz."
    if (len(data) - 44) / (rate * 2) > MAX_AUDIO_SECONDS:
        return f"Please keep questions under {MAX_AUDIO_SECONDS} seconds."
    return ""


@app.post("/api/transcribe")
async def transcribe(audio: UploadFile = File(...)) -> dict:
    data = await audio.read(MAX_AUDIO_BYTES + 1)
    if len(data) > MAX_AUDIO_BYTES:
        raise HTTPException(413, "That recording is too long.")
    if problem := wav_problem(data):
        raise HTTPException(415, problem)
    try:
        text = await run_in_threadpool(llm.transcribe, data, purpose="voice:transcribe")
    except llm.LLMError as exc:
        raise _model_error(exc, "I couldn't hear that clearly. Try again or type your question.") from None
    if not text:
        raise HTTPException(422, "I didn't catch any words. Try again a little closer to the mic.")
    return {"text": text}


class AskIn(BaseModel):
    question: str = Field(min_length=2, max_length=300)


@app.post("/api/ask")
def ask_saves(body: AskIn) -> dict:
    p = learner.load()
    level = learner.level(p, p["topic"]) if p["topic"] else 2
    try:
        result = ask.answer(body.question, level)
    except (llm.LLMError, TypeError, AttributeError, KeyError, ValueError) as exc:
        raise _model_error(exc, "I couldn't answer that right now. Try again.") from None
    return {**{k: result[k] for k in ("question", "answer", "follow_up", "found")},
            "mode": result.get("mode", "keyword"), "saves_read": result.get("saves_read", 0),
            "source_cards": [tools.card(s) for s in result["cited"]]}


@app.get("/api/eval")
def eval_pairs() -> dict:
    """The blind pairs: no method labels and no Wonder verdicts until every pair is picked."""
    pairs, picks = evaluation.load_pairs(), evaluation.load_picks()
    blind = [{"id": p["id"], "save_title": p["save_title"], "topic": p["topic"], "A": p["A"], "B": p["B"]} for p in pairs]
    done = bool(pairs) and all(p["id"] in picks for p in pairs)
    return {"pairs": blind, "picks": picks, "done": done, "results": evaluation.results() if done else None}


class PickIn(BaseModel):
    id: str = Field(pattern=r"^[0-9a-f]{10}$")
    choice: str = Field(pattern="^(A|B|tie)$")


@app.post("/api/eval/pick")
def eval_pick(body: PickIn) -> dict:
    if body.id not in {p["id"] for p in evaluation.load_pairs()}:
        raise HTTPException(404, "Unknown pair.")
    picks = evaluation.load_picks()
    if all(p["id"] in picks for p in evaluation.load_pairs()):
        raise HTTPException(409, "Results are revealed; picks are final. Build a new round for another blind test.")
    picks[body.id] = body.choice
    evaluation.PICKS.write_text(json.dumps(picks, indent=2), encoding="utf-8")
    done = all(p["id"] in picks for p in evaluation.load_pairs())
    if done:
        evaluation.report()
    return {"picked": len(picks), "done": done}


@app.get("/api/graph")
def brain_graph() -> dict:
    g = tools.graph()
    held = evaluation.held_out_ids()
    return {**g, "saves": [{**s, "held_out": s["id"] in held} for s in g["saves"]]}


def done_days(p: dict) -> list[int]:
    """A day is done once a quiz answer was given on any lesson built from that day's saves."""
    quizzed = set()
    started = p.get("plan_started", "")   # quiz answers from an earlier plan don't count
    for ev in p.get("history", []):
        if ev.get("kind") in ("quiz_right", "quiz_wrong") and ev.get("lesson") and ev.get("ts", "") >= started:
            path = llm.DATA / "lessons" / f"{ev['lesson']}.json"
            if path.exists():
                quizzed.add(tuple(sorted(json.loads(path.read_text(encoding="utf-8")).get("save_ids", []))))
    days = (p.get("plan") or {}).get("days", [])
    return [d["day"] for d in days if tuple(sorted(d["saves"])) in quizzed]


DEMO = llm.setting("TUTOR_DEMO") == "1"   # enables the demo-only time travel button (env var or .env)


@app.get("/api/progress")
def my_progress() -> dict:
    p = learner.load()
    return {**progress.summary(p, evaluation.held_out_ids(), done_days(p)), "demo": DEMO}


@app.get("/api/review")
def review(limit: int = 5) -> dict:
    p = learner.load()
    return {"questions": progress.review_questions(p, evaluation.held_out_ids(), max(1, min(limit, 8)))}


class AdvanceIn(BaseModel):
    days: int = Field(ge=1, le=30)


@app.post("/api/demo/advance")
def demo_advance(body: AdvanceIn) -> dict:
    if not DEMO:
        raise HTTPException(404, "Not found.")
    with learner.editing() as p:
        p["demo_offset_days"] = int(p.get("demo_offset_days", 0) or 0) + body.days
    return {"demo_offset_days": p["demo_offset_days"]}


class TeachIn(BaseModel):
    lesson_id: str = Field(pattern=r"^[0-9a-f]{16}$")
    text: str = Field(min_length=20, max_length=teachback.MAX_CHARS)


@app.post("/api/teachback")
def teach_back(body: TeachIn) -> dict:
    path = llm.DATA / "lessons" / f"{body.lesson_id}.json"
    if not path.exists():
        raise HTTPException(404, "Lesson not found.")
    lesson_data = json.loads(path.read_text(encoding="utf-8"))
    try:
        result = teachback.grade(lesson_data, body.text)
    except (llm.LLMError, ValueError, TypeError, KeyError) as exc:
        raise _model_error(exc, "Yaad couldn't check that right now. Try again.") from None
    with learner.editing() as p:            # a teach-back is a review: progress.py reads the score from detail
        learner.apply_feedback(p, "teachback", body.lesson_id, lesson_data.get("topic") or p.get("topic", ""), "", str(result["score"]))
    return result


@app.get("/api/spend")
def spend() -> dict:
    totals = llm.spend()
    return {"by_model": totals, "total_usd": round(sum(totals.values()), 4)}


class MasterIn(BaseModel):
    topic: str = Field(max_length=120)


def _topic_or_400(topic: str) -> str:
    if topic not in tools.topic_map():
        raise HTTPException(400, "Unknown topic.")
    return topic


@app.post("/api/masterclass")
def masterclass_start(body: MasterIn) -> dict:
    """Connect the dots for one topic: returns the cached result or starts/joins the background build."""
    p = learner.load()
    return masterclass.start(_topic_or_400(body.topic), learner.level(p, body.topic))


@app.get("/api/masterclass")
def masterclass_status(topic: str) -> dict:
    return masterclass.status(_topic_or_400(topic))


def _streamed(handler, body) -> StreamingResponse:
    """Run a normal handler in a worker thread and stream Yaad's live thoughts as NDJSON, then the result.
    POST (not EventSource) so the X-Yaadsi guard still applies; a disconnect lets the build finish and cache."""
    channel, box = thoughts.Channel(), {}

    def work() -> None:
        with thoughts.use(channel):
            try:
                box["data"] = handler(body)
            except HTTPException as exc:
                box["error"] = exc.detail
            except Exception:   # never leave the stream hanging on an unexpected error
                box["error"] = "Something went wrong. Try again."

    worker = threading.Thread(target=work, daemon=True)
    worker.start()

    def lines():
        while True:
            worker.join(0.25)
            for text in channel.drain():
                yield json.dumps({"type": "thought", "text": text}, ensure_ascii=False) + "\n"
            if not worker.is_alive():
                break
        end = {"type": "error", "detail": box["error"]} if "error" in box else {"type": "done", "data": box.get("data")}
        yield json.dumps(end, ensure_ascii=False) + "\n"

    return StreamingResponse(lines(), media_type="application/x-ndjson", headers={"Cache-Control": "no-cache"})


@app.post("/api/lesson/stream")
def lesson_stream(body: LessonIn) -> StreamingResponse:
    return _streamed(lesson, body)


@app.post("/api/ask/stream")
def ask_stream(body: AskIn) -> StreamingResponse:
    return _streamed(ask_saves, body)


class AddIn(BaseModel):
    url: str = Field(default="", max_length=ingest.MAX_URL)
    note: str = Field(default="", max_length=ingest.MAX_NOTE)
    text: str = Field(default="", max_length=ingest.MAX_TEXT)


def add_resource(body: AddIn) -> dict:
    """A link or note the learner found: read it, understand it, file it under a topic."""
    if not llm.has_keys():
        raise HTTPException(400, "Replay mode: adding links needs your Meta and Baseten keys in .env.")
    try:
        return ingest.add(body.url, body.note, body.text)
    except ingest.IngestError as exc:
        raise HTTPException(400, str(exc)) from None
    except (llm.LLMError, ValueError, OverflowError, TypeError, AttributeError, KeyError) as exc:   # after IngestError (a ValueError)
        raise _model_error(exc, "Yaad couldn't understand that right now. Try again.") from None


@app.post("/api/add")
def add_plain(body: AddIn) -> dict:
    return add_resource(body)


@app.post("/api/add/stream")
def add_stream(body: AddIn) -> StreamingResponse:
    return _streamed(add_resource, body)


class TodayIn(BaseModel):
    more: int = Field(default=0, ge=0, le=20)


@app.post("/api/today")   # POST: it can start a paid pre-warm, so the X-Yaadsi guard must apply
def today_view(body: TodayIn) -> dict:
    """Home: the backlog in numbers and today's short lesson, picked for you (pre-warmed so Start is instant)."""
    p = learner.load()
    pick, day = today.pick_today(p, body.more)
    if p.get("today") != day:   # remember the day's picks so nothing can swap them mid-day
        with learner.editing() as fresh:
            fresh["today"] = day
    if pick["save_ids"] and not pick["done"]:
        prewarm.warm_mini(today.mini_spec(pick["save_ids"], p))
    return {"backlog": today.backlog(p), "today": pick, "minutes_goal": today.TODAY_MINUTES,
            "nudge": {"text": "Daily nudge on WhatsApp at 8 pm", "status": "Coming in the Yaadsi app"}}
