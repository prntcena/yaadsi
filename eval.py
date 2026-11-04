#!/usr/bin/env python3
"""Blind eval: does the full system (best-of-3 + learned style rules) teach better than one plain draft?

    python eval.py build [--force]   # new pairs from unseen held-out saves + Muse Wonder's verdicts (both orders)
    python eval.py report            # write out/eval-report.md from Wonder's verdicts and the founder's picks

Sides are drawn at random (balanced) per build and stored, so a rebuild is not predictable. Each build uses
saves never shown in an earlier round, never studied in a lesson, and the planner keeps them out of plans.
A build only replaces the old pairs (and the founder's picks) when every pair succeeds, unless --force.
"""
from __future__ import annotations

import hashlib
import json
import secrets
import sys
from concurrent.futures import ThreadPoolExecutor

import improve
import lesson as lessons
import llm
import profile as learner
import tools

EVAL = llm.DATA / "eval"
PAIRS = EVAL / "pairs.json"
PICKS = EVAL / "picks.json"
USED = EVAL / "used.json"          # saves shown in any round so far (never reused)
HELD_OUT = EVAL / "held_out.json"  # the planner keeps these out of study plans
N_PAIRS = 8
LEVEL = 2
MINUTES = 15


def _read(path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default
    except (OSError, ValueError):
        return default


def _write(path, value) -> None:
    EVAL.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(value, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def held_out_ids() -> set[str]:
    return set(_read(HELD_OUT, []))


def studied(profile: dict) -> set[str]:
    """Saves in the current plan or behind any lesson the learner gave feedback on."""
    ids = {sid for d in (profile.get("plan") or {}).get("days", []) for sid in d["saves"]}
    for ev in profile.get("history", []):
        lesson = _read(lessons.CACHE / f"{ev.get('lesson', '')}.json", {}) if ev.get("lesson") else {}
        ids.update(lesson.get("save_ids", []))
    return ids


def held_out(profile: dict) -> list[str]:
    """Learnable saves never studied and never shown in an earlier round, spread across topics."""
    excluded = studied(profile) | set(_read(USED, []))
    by_topic: dict[str, list[str]] = {}
    for name, entry in tools.topic_map().items():
        for sid in entry["saves"]:
            s = tools.saves().get(sid)
            if s and sid not in excluded and s.get("learnable", True) and len(s["text"]) > 400:
                by_topic.setdefault(name, []).append(sid)
    for ids in by_topic.values():
        ids.sort(key=lambda i: hashlib.sha256(i.encode()).hexdigest())
    chosen: list[str] = []
    while len(chosen) < N_PAIRS and any(by_topic.values()):   # round-robin over topics
        for ids in by_topic.values():
            while ids and ids[0] in chosen:
                ids.pop(0)
            if ids and len(chosen) < N_PAIRS:
                chosen.append(ids.pop(0))
    return chosen


def _topic_of(sid: str) -> str:
    return next((name for name, e in tools.topic_map().items() if sid in e["saves"]), "General")


def _view(lesson: dict) -> dict:
    return {k: lesson.get(k) for k in ("title", "sections", "quiz", "extra", "words", "mermaid")}


def build_pair(sid: str, rules: list[str], system_left: bool) -> dict:
    topic = _topic_of(sid)
    single = lessons.draft([sid], LEVEL, topic, MINUTES, [])
    system = improve.best_of([sid], LEVEL, topic, MINUTES, rules, 3)
    a, b = (system, single) if system_left else (single, system)
    verdict = improve.judge_pair_both_orders(a, b, [sid], LEVEL)
    return {"id": secrets.token_hex(5), "save_id": sid, "save_title": tools.saves()[sid].get("title", ""),
            "topic": topic, "level": LEVEL, "system_side": "A" if system_left else "B",
            "system_judged": system.get("judge", {}).get("drafts", 1) > 1,
            "A": _view(a), "B": _view(b), "wonder": verdict, "rules": rules}


def build(force: bool = False) -> None:
    profile = learner.load()
    rules = profile.get("style_rules", [])
    ids = held_out(profile)
    if len(ids) < N_PAIRS:
        print(f"only {len(ids)} unseen held-out saves left")
    if not ids:
        sys.exit("no unseen held-out saves; nothing to build")
    sides = [True] * (len(ids) // 2) + [False] * (len(ids) - len(ids) // 2)
    secrets.SystemRandom().shuffle(sides)            # balanced, unpredictable sides
    print(f"{len(ids)} held-out saves; rules in use: {rules or '(none yet)'}")
    pairs, failed = [], []
    with ThreadPoolExecutor(4) as pool:
        for sid, result in zip(ids, pool.map(lambda a: _safe(build_pair, a[0], rules, a[1]), zip(ids, sides))):
            if isinstance(result, dict):
                pairs.append(result)
                print(f"  {sid}: wonder -> {result['wonder']['winner']} (system was {result['system_side']})")
            else:
                failed.append(sid)
                print(f"  {sid}: failed ({result})")
    if failed and not force:
        sys.exit(f"{len(failed)} pair(s) failed; kept the old pairs and picks. Re-run, or use --force to accept "
                 f"{len(pairs)} pairs.")
    if not pairs:
        sys.exit("no pairs built; kept the old pairs and picks")
    _write(PAIRS, pairs)
    _write(USED, sorted(set(_read(USED, [])) | {p["save_id"] for p in pairs}))
    _write(HELD_OUT, sorted(held_out_ids() | {p["save_id"] for p in pairs}))
    if PICKS.exists():
        PICKS.unlink()   # old picks belong to old pairs
    print(f"wrote {len(pairs)} pairs; spend: {llm.spend()}")


def _safe(fn, *args):
    try:
        return fn(*args)
    except (llm.LLMError, ValueError, TypeError, KeyError, AttributeError) as exc:
        return str(exc)[:120]


def load_pairs() -> list[dict]:
    pairs = _read(PAIRS, [])
    return [p for p in pairs if isinstance(p, dict) and {"id", "A", "B", "system_side", "wonder"} <= p.keys()] \
        if isinstance(pairs, list) else []


def load_picks() -> dict:
    picks = _read(PICKS, {})
    ids = {p["id"] for p in load_pairs()}
    return {k: v for k, v in picks.items() if k in ids} if isinstance(picks, dict) else {}


def results() -> dict:
    """Win rates for the full system, from Wonder (both orders must agree) and from the founder."""
    pairs, picks = load_pairs(), load_picks()

    def outcome(choice: str, system_side: str) -> str:
        return "tie" if choice not in ("A", "B") else ("system" if choice == system_side else "single")

    rows = []
    for p in pairs:
        row = {"id": p["id"], "title": p["save_title"], "topic": p["topic"],
               "wonder": outcome(p["wonder"]["winner"], p["system_side"]), "wonder_why": p["wonder"]["why"],
               "system_side": p["system_side"]}
        if p["id"] in picks:
            row["human"] = outcome(picks[p["id"]], p["system_side"])
        rows.append(row)

    def tally(key: str) -> dict:
        votes = [r[key] for r in rows if key in r]
        return {"n": len(votes), "system": votes.count("system"), "single": votes.count("single"), "tie": votes.count("tie")}

    both = [r for r in rows if "human" in r]
    return {"pairs": len(pairs), "picked": len(picks), "wonder": tally("wonder"), "human": tally("human"),
            "agreement": {"n": len(both), "agree": sum(1 for r in both if r["human"] == r["wonder"])},
            "rows": rows, "rules": pairs[0]["rules"] if pairs else []}


def report() -> str:
    r = results()
    system_desc = ("best-of-3 Muse Spark Open drafts picked by Muse Wonder, with these style rules learned from the "
                   f"founder's feedback: {'; '.join(r['rules'])}" if r["rules"] else
                   "best-of-3 Muse Spark Open drafts picked by Muse Wonder (no style rules had been learned yet)")
    lines = ["# Blind eval: full system vs one plain draft", "",
             f"Full system = {system_desc}. Plain = one Muse Spark Open draft, no rules. Same saved link, same level "
             f"({LEVEL}/5). Sides were shuffled at random (balanced) for this round.", "",
             "Caveats: n is small. Muse Wonder also picked the full system's best draft, so its vote is not independent; "
             "it judged every pair in both orders and only a verdict both orders agree on counts (else a tie). "
             "The founder's picks are the independent signal.", "",
             "| Judge | Pairs | Full system wins | Plain wins | Ties |", "|---|---|---|---|---|"]
    for name, key in (("Founder (blind)", "human"), ("Muse Wonder (both orders)", "wonder")):
        t = r[key]
        lines.append(f"| {name} | {t['n']} | {t['system']} | {t['single']} | {t['tie']} |")
    a = r["agreement"]
    lines += ["", f"Founder and Wonder agreed on {a['agree']} of {a['n']} pairs.", "",
              "| Saved link | Topic | Full system was | Founder | Wonder | Wonder's reason |", "|---|---|---|---|---|---|"]
    for row in r["rows"]:
        clean = lambda t: " ".join(str(t).replace("|", "/").split())
        lines.append(f"| {clean(row['title'])} | {clean(row['topic'])} | {row['system_side']} | {row.get('human', '-')} | "
                     f"{row['wonder']} | {clean(row['wonder_why'])} |")
    text = "\n".join(lines) + "\n"
    llm.OUT.mkdir(exist_ok=True)
    (llm.OUT / "eval-report.md").write_text(text, encoding="utf-8")
    return text


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "build"
    if cmd == "build":
        build(force="--force" in sys.argv)
    elif cmd == "report":
        print(report())
    else:
        sys.exit("usage: eval.py build [--force] | report")
