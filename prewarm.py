"""Instant lessons: build the plan's lessons in the background (Spark Open's 2M tokens/minute makes this cheap).

Every plan day at the learner's current level first, then one level easier and harder for the next 2 undone days,
so "too hard" / "too easy" are instant too. Same best-of-3 lessons a click would build (same cache keys);
lesson.make_lesson's per-key lock means a click on a day being built waits for it instead of paying twice.
Set TUTOR_PREWARM=0 to turn it off."""
from __future__ import annotations

import os
import threading
from concurrent.futures import ThreadPoolExecutor

import llm
import lesson
import profile as learner

MAX_JOBS = 20          # budget guard per schedule (~$0.10-0.15 per best-of-3 lesson)
BEST_OF = 3            # what /api/lesson builds by default, so the keys match
NEIGHBOUR_DAYS = 2

_pool = ThreadPoolExecutor(4, thread_name_prefix="prewarm")
_lock = threading.Lock()
_state = {"generation": 0, "pending": set(), "failed": set(), "dropped": 0}   # pending = queued or building


def enabled() -> bool:
    return llm.setting("TUTOR_PREWARM", "1") != "0" and llm.has_keys()   # env var or .env; never in replay mode


def _targets(p: dict, done: set[int]) -> list[tuple]:
    plan, topic = p.get("plan"), p.get("topic")
    if not plan or not topic:
        return []
    level, rules = learner.level(p, topic), p.get("style_rules") or []
    jobs = [(d["saves"], level, d["minutes"]) for d in plan["days"]]
    for d in [d for d in plan["days"] if d["day"] not in done][:NEIGHBOUR_DAYS]:
        jobs += [(d["saves"], lv, d["minutes"]) for lv in (level - 1, level + 1) if 1 <= lv <= 5]
    out, seen = [], set()
    for saves, lv, minutes in jobs:
        key = lesson.cache_key(saves, lv, rules, topic, minutes, BEST_OF)
        if key not in seen:
            seen.add(key)
            out.append((key, saves, lv, topic, minutes, rules))
    return out


def schedule(done: set[int] | None = None) -> int:
    """Queue the current plan's missing lessons; earlier queued jobs from an older plan/level/rules become no-ops."""
    if not enabled():
        return 0
    targets = [t for t in _targets(learner.load(), done or set()) if not lesson.is_cached(t[0])]
    queued, dropped = targets[:MAX_JOBS], max(0, len(targets) - MAX_JOBS)
    with _lock:
        _state["generation"] += 1
        gen = _state["generation"]
        _state.update(pending={t[0] for t in queued}, failed=set(), dropped=dropped)
    if dropped:
        print(f"prewarm: {dropped} lessons over the {MAX_JOBS}-lesson budget were not queued", flush=True)
    for t in queued:
        _pool.submit(_run, gen, *t)
    return len(queued)


def _run(gen: int, key: str, saves: list[str], level: int, topic: str, minutes: int, rules: list[str]) -> None:
    if gen != _state["generation"]:
        return   # the plan, level or rules changed since this was queued
    try:
        if not lesson.is_cached(key):
            lesson.make_lesson(saves, level, topic, minutes, rules, best_of=BEST_OF)
    except (llm.LLMError, ValueError, TypeError, AttributeError, KeyError, OSError):
        with _lock:
            if gen == _state["generation"]:
                _state["failed"].add(key)
    finally:
        with _lock:
            if gen == _state["generation"]:
                _state["pending"].discard(key)


_minis: set[str] = set()


def warm_mini(spec: dict) -> None:
    """Build today's lesson ahead of the click (once per key). Same best-of-3 lesson /api/lesson builds."""
    if not enabled():
        return
    key = lesson.cache_key(spec["save_ids"], spec["level"], spec["rules"], spec["topic"], spec["minutes"], BEST_OF)
    with _lock:
        if key in _minis or lesson.is_cached(key):
            return
        _minis.add(key)

    def run() -> None:
        try:
            lesson.make_lesson(spec["save_ids"], spec["level"], spec["topic"], spec["minutes"], spec["rules"], best_of=BEST_OF)
        except Exception:   # any failure: a later visit may try again
            with _lock:
                _minis.discard(key)
    _pool.submit(run)


def status() -> dict:
    """Readiness of the plan days at the current level (from the lesson cache, so it survives restarts)."""
    p = learner.load()
    plan, topic = p.get("plan"), p.get("topic")
    if not enabled() or not plan or not topic:
        return {"enabled": enabled(), "ready": 0, "total": 0, "running": 0, "failed": 0, "days_ready": []}
    level, rules = learner.level(p, topic), p.get("style_rules") or []
    days_ready = [d["day"] for d in plan["days"]
                  if lesson.is_cached(lesson.cache_key(d["saves"], level, rules, topic, d["minutes"], BEST_OF))]
    with _lock:
        running, failed = len(_state["pending"]), len(_state["failed"])
    return {"enabled": True, "ready": len(days_ready), "total": len(plan["days"]), "running": running,
            "failed": failed, "days_ready": days_ready}
