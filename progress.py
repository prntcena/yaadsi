"""Honest mastery from spaced reviews (Leitner boxes), rebuilt from the feedback history. No model calls.

A save moves up one box only when quizzed right on a *different day* than its last review; a miss drops it to
box 1 and makes it due now. Boxes 1-3 come due after 1, 3 and 7 days; box 4 is Mastered and re-checked after 21.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta

import llm
import tools

INTERVAL_DAYS = {1: 1, 2: 3, 3: 7, 4: 21}
MASTERED = 4
PASS = 0.5
TEACH_PASS = 70   # keep in step with teachback.PASS_SCORE


def clock(profile: dict) -> datetime:
    """'Now' for the learner: real time plus the demo offset (only ever set in demo mode)."""
    return datetime.now() + timedelta(days=float(profile.get("demo_offset_days", 0) or 0))


def _lesson_saves(lesson_id: str, cache: dict) -> list[str]:
    if lesson_id not in cache:
        path = llm.DATA / "lessons" / f"{lesson_id}.json"
        try:
            cache[lesson_id] = json.loads(path.read_text(encoding="utf-8")).get("save_ids", []) if path.exists() else []
        except (OSError, ValueError):
            cache[lesson_id] = []
    return cache[lesson_id]


def sessions(history: list[dict]) -> dict[str, list[tuple[date, float]]]:
    """save id -> [(day, share of quiz answers right that day)] in date order."""
    per: dict[str, dict[date, list[bool]]] = {}
    cache: dict = {}
    for ev in history:
        kind = ev.get("kind")
        if kind not in ("quiz_right", "quiz_wrong", "teachback") or not ev.get("lesson") or not ev.get("ts"):
            continue
        if kind == "teachback":   # explaining it well is the strongest recall signal: counts as a review answer
            try:
                right = int(ev.get("detail") or 0) >= TEACH_PASS
            except ValueError:
                continue
        else:
            right = kind == "quiz_right"
        try:
            day = datetime.fromisoformat(ev["ts"]).date()
        except ValueError:
            continue
        for sid in _lesson_saves(ev["lesson"], cache):
            per.setdefault(sid, {}).setdefault(day, []).append(right)
    return {sid: [(d, sum(v) / len(v)) for d, v in sorted(days.items())] for sid, days in per.items()}


def box_state(history_for_save: list[tuple[date, float]], today: date) -> dict:
    box, last, lapsed = 0, None, False
    for day, score in history_for_save:
        if box == 0:
            box, lapsed = 1, score < PASS
        elif score >= PASS:
            box = min(MASTERED, box + 1) if day != last and not lapsed else box
            lapsed = False
        else:
            box, lapsed = 1, True
        last = day
    if box == 0:
        return {"box": 0, "state": "new", "due_in_days": None, "last": None}
    due = last + timedelta(days=INTERVAL_DAYS[box])
    due_in = (due - today).days
    if lapsed or due_in <= 0:
        state = "review"
    else:
        state = "mastered" if box == MASTERED else "learning"
    return {"box": box, "state": state, "due_in_days": max(0, due_in), "last": last.isoformat()}


def summary(profile: dict, held_out: set[str], done_day_numbers: list[int]) -> dict:
    today = clock(profile).date()
    by_save = sessions(profile.get("history", []))
    all_saves = tools.saves()
    rows = []
    for sid, s in all_saves.items():
        if sid in held_out or not s.get("learnable", True):
            continue
        st = box_state(by_save.get(sid, []), today)
        rows.append({"id": sid, "title": s.get("title") or s["raw_title"], "platform": s["platform"],
                     "minutes": int(s.get("minutes") or 10),
                     "topic": next((n for n, e in tools.topic_map().items() if sid in e["saves"]), ""), **st})
    counts = {k: sum(1 for r in rows if r["state"] == k) for k in ("new", "learning", "review", "mastered")}
    topics = []
    for name, entry in tools.topic_map().items():
        mine = [r for r in rows if sid_in(r["id"], entry)]
        c = {k: sum(1 for r in mine if r["state"] == k) for k in ("new", "learning", "review", "mastered")}
        n = max(1, len(mine))
        status = ("Not started" if c["new"] == len(mine) else "Needs review" if c["review"] else
                  "Strong" if c["mastered"] / n >= 0.7 else "Getting there")
        topics.append({"name": name, "total": len(mine), **c, "status": status})
    plan_days = (profile.get("plan") or {}).get("days", [])
    minutes = sum(d["minutes"] for d in plan_days if d["day"] in set(done_day_numbers))
    return {"today": today.isoformat(), "demo_offset_days": profile.get("demo_offset_days", 0),
            "stats": {**counts, "minutes_learned": minutes, "days_done": len(done_day_numbers)},
            "topics": topics, "saves": [r for r in rows if r["state"] != "new"]}


def sid_in(sid: str, entry: dict) -> bool:
    return sid in entry["saves"]


def review_questions(profile: dict, held_out: set[str], limit: int = 5) -> list[dict]:
    """Quiz questions from cached lessons that cover saves due for review (newest lesson first). No model calls."""
    today = clock(profile).date()
    by_save = sessions(profile.get("history", []))
    due = {sid for sid, h in by_save.items() if sid not in held_out and box_state(h, today)["state"] == "review"}
    if not due:
        return []
    lessons = sorted((llm.DATA / "lessons").glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    out, used_saves = [], set()
    for path in lessons:
        try:
            l = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        covers = due & set(l.get("save_ids", []))
        if not covers or covers <= used_saves:
            continue
        used_saves |= covers
        for q in (l.get("quiz") or [])[:2]:
            if isinstance(q, dict) and isinstance(q.get("options"), list) and isinstance(q.get("answer_index"), int):
                out.append({"lesson_id": l["id"], "q": q["q"], "options": q["options"], "answer_index": q["answer_index"],
                            "why": q.get("why", ""), "about": tools.saves()[next(iter(covers))].get("title", "")})
        if len(out) >= limit:
            break
    return out[:limit]
