"""The Today home: your backlog in numbers and today's short lesson, picked for you. No model calls.

"From saved to learned": nobody should have to plan their own backlog. Each day Yaad picks a small, coherent
bite from the saves you have never studied (a quick one and a deep one from the same topic when it can),
sized to about 15 minutes, and the same pick stays put all day. Studying it (any quiz answer) marks it done.
"""
from __future__ import annotations

import hashlib
import json

import eval as evaluation
import llm
import profile as learner
import progress
import tools

TODAY_MINUTES = 15
MAX_SAVES = 3            # what a mini lesson takes (app.LessonIn.save_ids)
QUICK, DEEP = 5, 20      # minutes: a quick save vs a deep read
LONG = 60                # longer than this is a course: prefer shorter deep reads for a daily bite
MINI_MAX_MINUTES = TODAY_MINUTES   # a lesson built from saves is a daily-sized bite


def topic_of(sid: str) -> str:
    return next((name for name, entry in tools.topic_map().items() if sid in entry["saves"]), "")


def mini_spec(save_ids: list[str], profile: dict) -> dict:
    """Everything make_lesson needs for a lesson built from these saves. /api/lesson and pre-warm both use it,
    so a pre-warmed lesson has exactly the cache key a click asks for."""
    saves = tools.saves()
    topic = topic_of(save_ids[0]) or profile.get("topic", "")
    return {"save_ids": list(save_ids), "topic": topic, "level": learner.level(profile, topic),
            "minutes": min(MINI_MAX_MINUTES, sum(int(saves[s].get("minutes") or 10) for s in save_ids)),
            "rules": profile.get("style_rules") or []}


def _states(profile: dict) -> tuple[dict[str, dict], str]:
    today = progress.clock(profile).date()
    by_save = progress.sessions(profile.get("history", []))
    return {sid: progress.box_state(h, today) for sid, h in by_save.items()}, today.isoformat()


def _lessons(held: set[str] | None = None) -> dict[str, dict]:
    """Saves Yaad can teach (learnable, readable, in a topic). held = saves to leave out (the blind-test set)."""
    held = held or set()
    return {sid: s for sid, s in tools.saves().items()
            if s.get("learnable", True) and s.get("available", True) and sid not in held and topic_of(sid)}


def backlog(profile: dict) -> dict:
    """The five numbers partition everything saved: learned + waiting + fading + gone + not lessons = saved."""
    corpus = json.loads((llm.DATA / "corpus.json").read_text(encoding="utf-8"))
    states, today = _states(profile)
    lessons = _lessons()                       # counted honestly, blind-test saves included
    readable = [r for r in corpus if r.get("available", True)]
    gone = [r for r in corpus if not r.get("available", True)]
    learned = waiting = fading = 0
    waiting_minutes = []
    for sid, s in lessons.items():
        st = states.get(sid)
        if not st or not st.get("box"):
            waiting += 1
            waiting_minutes.append(int(s.get("minutes") or 10))
        elif st["state"] == "review" and (st.get("last") or "") < today:   # a same-day miss is still being learned
            fading += 1
        else:
            learned += 1
    return {"saved": len(corpus), "learned": learned, "waiting": waiting, "fading": fading, "gone": len(gone),
            "not_lessons": len(readable) - len(lessons),
            "quick": sum(1 for m in waiting_minutes if m <= QUICK), "deep": sum(1 for m in waiting_minutes if m >= DEEP),
            "read_for_you": {"slides": sum(len(s.get("images") or []) for s in tools.saves().values()),
                             "reels": sum(1 for s in tools.saves().values() if s.get("platform") == "instagram-reel")},
            "gone_items": [{"platform": r.get("platform", ""), "url": r.get("url", ""), "note": r.get("user_note", "")}
                           for r in gone]}


def _seed(today: str) -> str:
    """The day's pick changes daily when live. Replay mode (no keys) uses a fixed seed, so every visitor gets the
    picks whose lessons were made in advance (TUTOR_PICK_SEED=replay when making them)."""
    return llm.setting("TUTOR_PICK_SEED") or (today if llm.has_keys() else "replay")


def _rank(seed: str, key: str) -> str:
    return hashlib.sha256(f"{seed}|{key}".encode("utf-8")).hexdigest()


def _pick(pool: dict[str, dict], seed: str) -> list[str]:
    """A coherent bite: one topic, a deep save + a quick one when possible, about TODAY_MINUTES of reading."""
    by_topic: dict[str, list[str]] = {}
    for sid in pool:
        by_topic.setdefault(topic_of(sid), []).append(sid)
    mins = {sid: int(pool[sid].get("minutes") or 10) for sid in pool}
    for topic in sorted(by_topic, key=lambda t: _rank(seed, t)):
        ids = sorted(by_topic[topic], key=lambda s: _rank(seed, s))
        deep = sorted((s for s in ids if mins[s] > QUICK), key=lambda s: (mins[s] > LONG, _rank(seed, s)))   # courses last
        quick = [s for s in ids if mins[s] <= QUICK]
        if deep and quick:
            picked = [deep[0], quick[0]]
            budget = TODAY_MINUTES - min(mins[deep[0]], TODAY_MINUTES) - mins[quick[0]]
            picked += [s for s in quick[1:] if mins[s] <= budget][:MAX_SAVES - 2]
            return picked
    for topic in sorted(by_topic, key=lambda t: _rank(seed, t)):   # no topic has both: fill the budget in one topic
        picked, used = [], 0
        for sid in sorted(by_topic[topic], key=lambda s: (mins[s] > LONG, _rank(seed, s))):
            if len(picked) < MAX_SAVES and (not picked or used + mins[sid] <= TODAY_MINUTES):
                picked.append(sid)
                used += mins[sid]
        if picked:
            return picked
    return []


def pick_today(profile: dict, more: int = 0) -> tuple[dict, dict]:
    """(today's pick, the day's stored picks). The first pick of a date is stored in the profile, so a review, a plan
    lesson or a new save can't swap it mid-day; "Do one more" appends the next bite (only once the last one is done).
    more=N shows the Nth extra bite. Picks come only from saves never studied, never the blind-test set."""
    states, today = _states(profile)
    lessons = _lessons(evaluation.held_out_ids())
    studied_today = {sid for sid, st in states.items() if st.get("last") == today}
    stored = profile.get("today") or {}
    picks = [[sid for sid in p if sid in lessons] for p in stored.get("picks", [])] if stored.get("date") == today else []
    fresh = {sid: s for sid, s in lessons.items() if not (states.get(sid) or {}).get("box")}
    used = {sid for p in picks for sid in p}
    if not picks:
        picks = [_pick(fresh, _seed(today))]
        used = set(picks[0])
    while len(picks) - 1 < more and picks[-1] and all(sid in studied_today for sid in picks[-1]):
        nxt = _pick({sid: s for sid, s in fresh.items() if sid not in used}, f"{_seed(today)}+{len(picks)}")
        if not nxt:
            break
        picks.append(nxt)
        used |= set(nxt)
    current = picks[min(max(0, more), len(picks) - 1)]
    saves = tools.saves()
    source = sum(int(saves[s].get("minutes") or 10) for s in current)
    pick = {"save_ids": current, "cards": [tools.card(s) for s in current], "source_minutes": source,
            "taught_minutes": min(MINI_MAX_MINUTES, source) if current else 0,
            "topic": topic_of(current[0]) if current else "", "index": picks.index(current),
            "done": bool(current) and all(sid in studied_today for sid in current), "date": today}
    return pick, {"date": today, "picks": picks}
