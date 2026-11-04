"""Build one lesson from a day's saves with a Muse Spark Open tool loop.

The day's saves are put straight into the prompt (fast); the agent may call search_saves/read_save to
pull in related saves, then must answer with the lesson JSON. Lessons are cached by (saves, level, rules).
"""
from __future__ import annotations

import hashlib
import json
import sys
import threading

import agent
import llm
import thoughts
import tools

CACHE = llm.DATA / "lessons"
MAX_STEPS = 6
PROMPT_VERSION = 4   # bump when the lesson schema/prompt changes so cached lessons regenerate
WORD_LIMITS = {1: 120, 2: 200, 3: 300, 4: 550, 5: 900}
LEVEL_STYLE = {
    1: "Explain like the learner is 10 years old. Start with an everyday analogy. Very short sentences, no jargon.",
    2: "Beginner. Start with an everyday analogy, then the idea in plain words. Define any term you use.",
    3: "Intermediate summary notes: the key ideas and why they matter, with one concrete example.",
    4: "Detailed: how it works, trade-offs and a worked example. Technical terms are fine.",
    5: "Deep article: mechanisms, trade-offs, edge cases, and how the ideas connect to each other.",
}

SYSTEM = """You are a patient tutor who teaches one person from the links they saved.
Teaching level {level}/5: {style}
The whole lesson (all section bodies together) must be at most {words} words - count them. Aim for about {minutes} minutes.
Write short paragraphs separated by a blank line; lists use lines starting with "- ". No markdown headings or bold.
Ground everything in the saves. If you add something that is not in the saves, put it in "extra", not in sections.
Teach from first principles: {shape}
One of the 3 quiz questions must ask what breaks or goes wrong (e.g. "What fails first if usage grows 100x?").
{rules}
You may call search_saves / read_save to connect related saves. When ready, reply with ONLY this JSON:
{{"title": str,
 "sections": [{{"heading": str, "body": str}}],        // follow the first-principles shape above
 "diagram": {{"nodes": [{{"id": str, "label": str}}], "edges": [[from_id, to_id, optional_label]]}},  // 3-8 nodes showing how the idea flows
 "steps": [{{"node_id": str, "narration": str}}],      // walk the diagram nodes in teaching order, one short sentence each, at the same level
 "quiz": [{{"q": str, "options": [str, str, str, str], "answer_index": int, "why": str}}],  // exactly 3 questions
 "sources": [save_id],                                   // ids of saves you actually used
 "extra": [str]}}                                        // optional facts not from the saves"""


# First-principles shape (from "how to learn efficiently"): why it exists, the problem, the trade-offs, what breaks.
FP_HEADINGS = ["Why does this exist?", "What problem does it solve?", "Trade-offs", "What breaks at scale?"]
SHAPE = {
    "simple": ('use 2-3 sections: first an everyday analogy, then "Why it exists" and "What can go wrong", '
               "in very simple words."),
    "full": ("use exactly 4 sections with these headings, in this order: " + " | ".join(FP_HEADINGS) +
             '. For topics that are not technical, adapt the last one (e.g. "What breaks when you misuse it?"). '
             "Each section must come from the saves."),
}


def shape_for(level: int) -> str:
    return SHAPE["full" if level >= 3 else "simple"]


def shape_problems(lesson: dict, level: int) -> list[str]:
    """Levels 3+ must follow the 4 first-principles headings (loose match on key words)."""
    if level < 3:
        return []
    heads = " | ".join(str(s.get("heading", "")).lower() for s in lesson.get("sections") or [] if isinstance(s, dict))
    need = {"why": "why", "problem": "problem", "trade": "trade-off", "break": "break"}
    missing = [label for key, label in need.items() if key not in heads]
    return [f"use the 4 first-principles section headings ({' | '.join(FP_HEADINGS)}); missing: {', '.join(missing)}"] \
        if missing else []


def cache_key(save_ids: list[str], level: int, rules: list[str], topic: str, minutes: int, best_of: int = 1) -> str:
    raw = json.dumps([PROMPT_VERSION, sorted(save_ids), level, rules, topic, minutes, best_of])
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def validate(lesson: dict, known: set[str]) -> list[str]:
    problems = []
    if not lesson.get("title"):
        problems.append("missing title")
    sections = lesson.get("sections")
    if not isinstance(sections, list) or not sections or not all(isinstance(s, dict) and s.get("body") for s in sections):
        problems.append("sections must be a non-empty list of {heading, body}")
    quiz = lesson.get("quiz")
    if not isinstance(quiz, list) or not quiz:
        problems.append("quiz must have 3 questions")
    else:
        for q in quiz:
            opts = q.get("options") if isinstance(q, dict) else None
            if not isinstance(opts, list) or len(opts) < 2 or not isinstance(q.get("answer_index"), int) \
                    or not 0 <= q["answer_index"] < len(opts):
                problems.append("each quiz question needs options and a valid answer_index")
                break
    sources = lesson.get("sources")
    lesson["sources"] = [s for s in sources if isinstance(s, str) and s in known] if isinstance(sources, list) else []
    return problems


def animation_steps(lesson: dict, node_ids: dict[str, str]) -> list[dict]:
    """[{node: "n<i>", narration}] for the player; falls back to the diagram's own node order and labels."""
    steps = []
    for step in lesson.get("steps") or []:
        if isinstance(step, dict) and str(step.get("node_id")) in node_ids and step.get("narration"):
            steps.append({"node": node_ids[str(step["node_id"])], "narration": str(step["narration"])[:300]})
    if len(steps) < 2:
        labels = {str(n.get("id")): str(n.get("label", "")) for n in tools.diagram_parts(lesson.get("diagram"))["nodes"]}
        steps = [{"node": safe, "narration": labels.get(model_id, "")} for model_id, safe in node_ids.items()]
    return steps


def word_count(lesson: dict) -> int:
    return sum(len(str(s.get("body", "")).split()) for s in lesson.get("sections", []))


def make_lesson(save_ids: list[str], level: int, topic: str, minutes: int, rules: list[str] | None = None,
                use_cache: bool = True, best_of: int = 1) -> dict:
    """A cached lesson. best_of > 1 drafts several in parallel and lets Muse Wonder pick (improve.py)."""
    rules = rules or []
    key = cache_key(save_ids, level, rules, topic, minutes, best_of)
    path = CACHE / f"{key}.json"
    if use_cache and path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    with _key_lock(key):   # a click on a day being pre-warmed waits for that build instead of paying twice
        if use_cache and path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
        return _build(key, path, save_ids, level, topic, minutes, rules, best_of)


_guard = threading.Lock()
_locks: dict[str, threading.Lock] = {}


def _key_lock(key: str) -> threading.Lock:
    with _guard:
        return _locks.setdefault(key, threading.Lock())


def is_cached(key: str) -> bool:
    return (CACHE / f"{key}.json").exists()


def _build(key: str, path, save_ids: list[str], level: int, topic: str, minutes: int, rules: list[str],
           best_of: int) -> dict:
    if best_of > 1:
        import improve   # improve imports this module
        lesson = improve.best_of(save_ids, level, topic, minutes, rules, best_of)
    else:
        lesson = draft(save_ids, level, topic, minutes, rules)
    lesson["id"] = key
    llm.write_json(path, lesson)
    return lesson


def draft(save_ids: list[str], level: int, topic: str, minutes: int, rules: list[str] | None = None,
          temperature: float | None = None) -> dict:
    """One uncached lesson draft (the agent loop, a repair pass and a tightening pass)."""
    rules = rules or []
    known = set(tools.saves())
    material = [tools.read_save(sid) for sid in save_ids if sid in known]
    thoughts.focus(" ".join(f"{m['title']} {m['summary']} {' '.join(m['concepts'])}" for m in material))
    rule_text = ("What you know about this learner:\n- " + "\n- ".join(rules)) if rules else ""
    messages = [
        {"role": "system", "content": SYSTEM.format(level=level, style=LEVEL_STYLE[level], words=WORD_LIMITS[level], shape=shape_for(level),
                                                     minutes=minutes, rules=rule_text)},
        {"role": "user", "content": f"Topic: {topic}\nToday's saves:\n{json.dumps(material, ensure_ascii=False)}"},
    ]
    lesson, trace = agent.run(messages, purpose="lesson", max_steps=MAX_STEPS, temperature=temperature)

    problems = (validate(lesson, known) + shape_problems(lesson, level)) if lesson else ["reply was not the lesson JSON"]
    if problems:   # one repair pass in JSON mode, without tools
        messages.append({"role": "user", "content": "Fix these problems and reply with only the lesson JSON: "
                                                    + "; ".join(problems)})
        lesson = llm.chat_json(messages, purpose="lesson:repair", effort="low", max_tokens=6000)
        problems = validate(lesson, known)   # shape is best-effort after one repair; content validity is required
        if problems:
            raise llm.LLMError("lesson invalid after repair: " + "; ".join(problems))

    limit = WORD_LIMITS[level]
    if word_count(lesson) > limit * 1.25:   # the model drifts long; one tightening pass keeps levels distinct
        tightened = llm.chat_json([
            {"role": "system", "content": f"Shorten the lesson's section bodies to at most {limit} words in total. "
                                          f"Keep the same JSON shape, the same level of language "
                                          f"({LEVEL_STYLE[level]}) and every other field unchanged."},
            {"role": "user", "content": json.dumps(lesson, ensure_ascii=False)}],
            purpose="lesson:tighten", effort="low", max_tokens=6000)
        # take only the shortened sections; diagram, steps, quiz and title stay from the original
        if isinstance(tightened.get("sections"), list) and word_count(tightened) < word_count(lesson) \
                and not validate({**lesson, "sections": tightened["sections"]}, known):
            lesson["sections"] = tightened["sections"]

    # Every save planned for today stays visible as a source, even if a short lesson leaned on one.
    lesson["sources"] += [sid for sid in save_ids if sid in known and sid not in lesson["sources"]]
    mermaid_text, node_ids = tools.mermaid_with_ids(lesson.get("diagram") or {})
    lesson.update({"level": level, "topic": topic, "save_ids": save_ids, "words": word_count(lesson),
                   "word_limit": WORD_LIMITS[level], "mermaid": mermaid_text,
                   "animation": animation_steps(lesson, node_ids), "rules_used": rules,
                   "agent_trace": trace})
    return lesson


if __name__ == "__main__":   # python lesson.py <level> <save_id> [<save_id> ...]
    lvl, ids = int(sys.argv[1]), sys.argv[2:]
    result = make_lesson(ids, lvl, "test", 15, use_cache=False)
    print(json.dumps({k: result[k] for k in ("title", "words", "word_limit", "sources", "agent_trace")}, ensure_ascii=False))
    for s in result["sections"]:
        print(f"\n## {s['heading']}\n{s['body']}")
    print("\n" + result["mermaid"])
