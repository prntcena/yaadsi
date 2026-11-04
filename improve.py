"""Self-improvement without training: best-of-N drafts judged by Muse Wonder, and style rules learned
from the learner's feedback (prompt optimisation from preferences, the no-GPU cousin of DPO)."""
from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor

import lesson as lessons
import llm
import thoughts
import tools

DRAFT_TEMPERATURES = (0.4, 0.8, 1.0)
RULES_EVERY = 5          # feedback events between rule updates
MAX_RULES = 5

JUDGE_SYSTEM = """You are a strict teacher judging lesson drafts written for one learner from their saved links.
Target level {level}/5 ({style}); at most {words} words.{rules}
Score every draft 1-10 on:
- grounding: claims come from the source material (anything else is clearly marked as extra);
- level_fit: matches the target level and length;
- clarity: easy to follow, good example or analogy, the quiz checks real understanding.
Reply with ONLY JSON: {{"scores": {{"<draft id>": {{"grounding": int, "level_fit": int, "clarity": int}}}},
"best": "<draft id>", "why": str}}"""

PAIR_SYSTEM = """You compare two lessons written for the same learner from the same saved material.
Target level {level}/5 ({style}). Judge which one would teach this learner better: grounded in the material,
right level, clear. You don't know how they were made. Reply with ONLY JSON:
{{"winner": "A" | "B" | "tie", "why": str}}   // why: at most 2 short sentences"""

RULES_SYSTEM = """You study how one person learns. From their feedback log on lessons, write at most {n} short,
concrete style rules a tutor should follow for them (e.g. "Start with an everyday analogy before any definition",
"Keep lists to 3 items"). Only rules the evidence supports; keep earlier rules that still hold.
Reply with ONLY JSON: {{"rules": [str], "summary": str}}   // summary: one sentence on how they learn"""


def _rules_text(rules: list[str]) -> str:
    return ("\nThe learner's known preferences:\n- " + "\n- ".join(rules)) if rules else ""


def _public(lesson: dict) -> dict:
    """What a judge sees: the teaching content, not our bookkeeping."""
    return {k: lesson.get(k) for k in ("title", "sections", "quiz", "extra")}


def _material(save_ids: list[str]) -> list[dict]:
    return [tools.read_save(sid) for sid in save_ids if sid in tools.saves()]


def best_of(save_ids: list[str], level: int, topic: str, minutes: int, rules: list[str], n: int = 3) -> dict:
    """Draft n lessons in parallel (different temperatures), let Muse Wonder pick the best."""
    temps = list(DRAFT_TEMPERATURES[:n]) + [1.0] * max(0, n - len(DRAFT_TEMPERATURES))
    channel = thoughts.current()   # only the first draft streams its thinking to Yaad; the others stay silent
    thoughts.step(f"Writing {n} versions of your lesson at once…")

    def run(i: int, t: float) -> dict:
        with thoughts.use(channel if i == 0 else None):
            return lessons.draft(save_ids, level, topic, minutes, rules, t)

    with ThreadPoolExecutor(n) as pool:
        futures = [pool.submit(run, i, t) for i, t in enumerate(temps)]
        drafts = []
        for f in futures:
            try:
                drafts.append(f.result())
            except (llm.LLMError, ValueError, TypeError, AttributeError, KeyError):
                pass
    if not drafts:
        raise llm.LLMError("no usable drafts")
    if len(drafts) == 1:
        return {**drafts[0], "judge": {"drafts": 1, "why": "only one usable draft"}}

    ids = [f"D{i + 1}" for i in range(len(drafts))]
    thoughts.step(f"Muse Wonder is comparing the {len(drafts)} versions and picking the best…")
    payload = {"source_material": _material(save_ids),
               "drafts": {i: _public(d) for i, d in zip(ids, drafts)}}
    try:
        verdict = llm.chat_json([
            {"role": "system", "content": JUDGE_SYSTEM.format(level=level, style=lessons.LEVEL_STYLE[level],
                                                              words=lessons.WORD_LIMITS[level], rules=_rules_text(rules))},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}],
            purpose="improve:judge", model=llm.JUDGE_MODEL, effort="low", max_tokens=3000)
    except (llm.LLMError, ValueError):
        verdict = {}
    scores = verdict.get("scores") if isinstance(verdict.get("scores"), dict) else {}

    def total(i: str) -> int:
        s = scores.get(i) if isinstance(scores.get(i), dict) else {}
        return sum(int(v) for v in s.values() if isinstance(v, (int, float)))

    best = verdict.get("best") if verdict.get("best") in ids else max(ids, key=total)
    chosen = drafts[ids.index(best)]
    return {**chosen, "judge": {"drafts": len(drafts), "best": best, "why": short(verdict.get("why", "")),
                                "scores": {i: scores.get(i) for i in ids}, "temperatures": temps[:len(drafts)]}}


def short(text, limit: int = 300) -> str:
    """Trim on a sentence (or word) boundary, never mid-word."""
    text = " ".join(str(text or "").split())
    if len(text) <= limit:
        return text
    cut = text[:limit]
    end = max(cut.rfind(". "), cut.rfind("! "), cut.rfind("? "))
    return cut[:end + 1] if end > limit // 2 else cut[:cut.rfind(" ")] + "…"


def judge_pair(a: dict, b: dict, save_ids: list[str], level: int) -> dict:
    """One blind pairwise verdict. The judge is not told the learner's rules (the plain draft never saw them)."""
    payload = {"source_material": _material(save_ids), "A": _public(a), "B": _public(b)}
    verdict = llm.chat_json([
        {"role": "system", "content": PAIR_SYSTEM.format(level=level, style=lessons.LEVEL_STYLE[level])},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}],
        purpose="eval:judge", model=llm.JUDGE_MODEL, effort="low", max_tokens=2000)
    winner = verdict.get("winner") if verdict.get("winner") in ("A", "B", "tie") else "invalid"
    return {"winner": winner, "why": short(verdict.get("why", ""))}


def judge_pair_both_orders(a: dict, b: dict, save_ids: list[str], level: int) -> dict:
    """Judge (A, B) and then (B, A); a win counts only if both orders agree, so position bias becomes a tie."""
    first = judge_pair(a, b, save_ids, level)
    swapped = judge_pair(b, a, save_ids, level)
    unswap = {"A": "B", "B": "A"}.get(swapped["winner"], swapped["winner"])
    winner = first["winner"] if first["winner"] == unswap and first["winner"] in ("A", "B") else "tie"
    return {"winner": winner, "why": first["why"] if winner != "tie" else
            (first["why"] if first["winner"] == "tie" else f"Orders disagreed. {first['why']}"),
            "orders": {"ab": first["winner"], "ba_unswapped": unswap}}


def _feedback_log(profile: dict) -> list[dict]:
    """Feedback events joined with what each lesson looked like, so the rules rest on evidence."""
    rows = []
    for ev in profile.get("history", [])[-40:]:
        row = {"kind": ev.get("kind"), "topic": ev.get("topic"), "section": ev.get("section", "")}
        path = lessons.CACHE / f"{ev.get('lesson', '')}.json"
        if ev.get("lesson") and path.exists():
            l = json.loads(path.read_text(encoding="utf-8"))
            row.update(level=l.get("level"), words=l.get("words"),
                       opening=str((l.get("sections") or [{}])[0].get("body", ""))[:160],
                       section_text=next((str(s.get("body", ""))[:200] for s in l.get("sections", [])
                                          if s.get("heading") == ev.get("section")), ""))
        if ev.get("detail"):
            row["detail"] = ev["detail"]
        rows.append(row)
    return rows


def maybe_learn_rules(profile: dict) -> bool:
    """Every RULES_EVERY new feedback events, ask Muse Wonder to (re)write the learner's style rules."""
    history = profile.get("history", [])
    if len(history) - profile.get("rules_at", 0) < RULES_EVERY:
        return False
    try:
        result = llm.chat_json([
            {"role": "system", "content": RULES_SYSTEM.format(n=MAX_RULES)},
            {"role": "user", "content": json.dumps({"current_rules": profile.get("style_rules", []),
                                                    "feedback": _feedback_log(profile)}, ensure_ascii=False)}],
            purpose="improve:rules", model=llm.JUDGE_MODEL, effort="low", max_tokens=2000)
    except (llm.LLMError, ValueError):
        return False
    rules = [str(r).strip()[:160] for r in (result.get("rules") or []) if str(r).strip()][:MAX_RULES]
    if not rules:
        return False
    old = set(profile.get("style_rules", []))
    profile["style_rules"] = rules
    profile["learner_summary"] = str(result.get("summary", ""))[:300]
    profile["rules_at"] = len(history)
    profile.setdefault("rules_history", []).append({"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "rules": rules,
                                                    "new": [r for r in rules if r not in old]})
    return True
