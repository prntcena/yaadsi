"""The learner profile: goal, time budget, depth, a per-topic level that feedback moves, and the
style rules improve.py learns from the feedback history."""
from __future__ import annotations

import json
import os
import threading
import time
from contextlib import contextmanager
from pathlib import Path

import llm

PATH = Path(os.environ.get("TUTOR_PROFILE") or llm.DATA / "profile.json")   # override for demos/tests
DEFAULT = {"goal": "", "topic": "", "deadline": "", "minutes_per_day": 20, "depth": 2,
           "formats": ["diagram"], "levels": {}, "style_rules": [], "learner_summary": "", "rules_at": 0,
           "rules_history": [], "history": [], "plan": None}
_lock = threading.RLock()   # feedback, rule learning and plan changes can overlap


def load() -> dict:
    with _lock:
        if PATH.exists():
            return {**json.loads(json.dumps(DEFAULT)), **json.loads(PATH.read_text(encoding="utf-8"))}
        return json.loads(json.dumps(DEFAULT))


def save(profile: dict) -> None:
    with _lock:
        llm.DATA.mkdir(exist_ok=True)
        tmp = PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(profile, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(PATH)


@contextmanager
def editing():
    """Load, let the caller change it, save - atomically with respect to other threads."""
    with _lock:
        profile = load()
        yield profile
        save(profile)


def level(profile: dict, topic: str) -> int:
    return int(profile["levels"].get(topic, profile.get("depth", 2)))


def apply_feedback(profile: dict, kind: str, lesson_id: str, topic: str, section: str = "", detail: str = "") -> int:
    """too_hard / too_easy move the topic level; every event is logged for rule learning."""
    current = level(profile, topic)
    if kind == "too_hard":
        current = max(1, current - 1)
    elif kind == "too_easy":
        current = min(5, current + 1)
    profile["levels"][topic] = current
    import progress   # the learner's clock (real time + demo offset)
    profile["history"].append({"ts": progress.clock(profile).strftime("%Y-%m-%dT%H:%M:%S"), "kind": kind, "lesson": lesson_id,
                               "topic": topic, "section": section[:120], "detail": detail[:200],
                               "level_after": current})
    return current
