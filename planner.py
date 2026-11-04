"""Deterministic day plan (no LLM) so the setup preview updates instantly while the sliders move."""
from __future__ import annotations

import json
import math
from datetime import date, timedelta

import tools

MIN_ITEM_MINUTES = 5


def _held_out() -> set[str]:
    path = tools.llm.DATA / "eval" / "held_out.json"
    try:
        return set(json.loads(path.read_text(encoding="utf-8"))) if path.exists() else set()
    except (OSError, ValueError):
        return set()


def plan(topic: str, deadline: str, minutes_per_day: int, goal: str = "", start: date | None = None,
         order: list[str] | None = None) -> dict:
    entry = tools.topic_map().get(topic)
    if not entry:
        return {"error": f"unknown topic {topic}"}
    start = start or date.today()
    try:
        end = date.fromisoformat(deadline)
    except ValueError:
        return {"error": "pick a deadline"}
    days_available = max(1, (end - start).days + 1)
    minutes_per_day = max(5, int(minutes_per_day))

    all_saves = tools.saves()
    held_out = _held_out()   # saves reserved for the blind test never enter a study plan
    items = [all_saves[sid] for sid in entry["saves"] if sid in all_saves and sid not in held_out]
    held_out_here = sum(1 for sid in entry["saves"] if sid in held_out)
    # Saves that match the goal come first; within each group, easy first.
    items.sort(key=lambda s: (-min(tools.relevance(goal, s), 6) if goal else 0,
                              s.get("difficulty", 3), s.get("minutes", 10)))
    if order:   # an explicit learning order (Connect the dots) wins; saves it doesn't place keep the order above
        rank = {sid: i for i, sid in enumerate(order)}
        items.sort(key=lambda s: rank.get(s["id"], len(rank)))
    # A lesson teaches a save at the learner's depth; cap any one save at a day's budget.
    sized = [(s["id"], min(max(int(s.get("minutes") or 10), MIN_ITEM_MINUTES), minutes_per_day)) for s in items]

    days = _pack(sized, minutes_per_day)

    total = sum(m for _, m in sized)
    fits = len(days) <= days_available
    out_days = [{"day": i + 1, "date": (start + timedelta(days=i)).isoformat(), "saves": ids, "minutes": m,
                 "titles": [all_saves[s].get("title", "") for s in ids]} for i, (ids, m) in enumerate(days)]
    return {"topic": topic, "days": out_days, "fits": fits, "saves": len(sized), "held_out": held_out_here,
            "total_minutes": total, "days_needed": len(days), "days_available": days_available,
            "minutes_needed_per_day": _minutes_to_fit(items, days_available, minutes_per_day)}


def _pack(sized: list[tuple[str, int]], minutes_per_day: int) -> list[tuple[list[str], int]]:
    days, current, used = [], [], 0
    for sid, minutes in sized:
        if current and used + minutes > minutes_per_day:
            days.append((current, used))
            current, used = [], 0
        current.append(sid)
        used += minutes
    if current:
        days.append((current, used))
    return days


def _minutes_to_fit(items: list[dict], days_available: int, current: int) -> int:
    """The smallest daily budget (5-minute steps, above the current one) whose packing fits the deadline."""
    for m in range(current + 5, 241, 5):
        sized = [(s["id"], min(max(int(s.get("minutes") or 10), MIN_ITEM_MINUTES), m)) for s in items]
        if len(_pack(sized, m)) <= days_available:
            return m
    return 240
