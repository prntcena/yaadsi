"""Re-make the demo lessons that Muse Wonder never judged (judge.why empty), as a judged best of 3.

    TUTOR_DATA=demo TUTOR_PROFILE=<a throwaway path> python scripts/rejudge_demo_lessons.py    # keys in .env

The cached files would otherwise be served as they are, so this rebuilds each one under its own cache key.
A lesson the judge still can't reach stays as it was and is listed at the end. About $0.03 a lesson.
"""
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import llm                      # noqa: E402
import profile as learner       # noqa: E402
import today                    # noqa: E402
from lesson import CACHE, cache_key, make_lesson   # noqa: E402

BEST_OF = 3


def unjudged() -> list[dict]:
    lessons = []
    for path in sorted(CACHE.glob("*.json")):
        lesson = json.loads(path.read_text(encoding="utf-8"))
        if not (lesson.get("judge") or {}).get("why"):
            lessons.append(lesson)
    return lessons


def spec_for(lesson: dict) -> dict | None:
    """The make_lesson arguments that give this file's cache key, or None when they can't be rebuilt."""
    spec = today.mini_spec(lesson["save_ids"], learner.load())
    spec.update(level=lesson["level"], topic=lesson["topic"], rules=lesson.get("rules_used") or [])
    key = cache_key(spec["save_ids"], spec["level"], spec["rules"], spec["topic"], spec["minutes"], BEST_OF)
    return spec if key == lesson["id"] else None


def rebuild(lesson: dict) -> str:
    spec = spec_for(lesson)
    if spec is None:
        return "skipped: its cache key can't be rebuilt from the file"
    try:
        new = make_lesson(spec["save_ids"], spec["level"], spec["topic"], spec["minutes"], spec["rules"],
                          use_cache=False, best_of=BEST_OF)
    except llm.LLMError as exc:
        return f"failed: {type(exc).__name__}"
    if (new.get("judge") or {}).get("why"):
        return "judged"
    llm.write_json(CACHE / f"{lesson['id']}.json", lesson)   # no better than before: keep the old one
    return "still unjudged (kept the old file)"


def main() -> None:
    if llm.DATA.name != "demo":
        sys.exit("run with TUTOR_DATA=demo")
    if not llm.has_keys():
        sys.exit("needs the keys in .env")
    if learner.load().get("history"):
        sys.exit("use a throwaway TUTOR_PROFILE")
    todo = unjudged()
    print(f"{len(todo)} unjudged lessons", flush=True)
    spent0 = sum(llm.spend().values())
    with ThreadPoolExecutor(4) as pool:
        results = list(pool.map(rebuild, todo))
    for lesson, result in zip(todo, results):
        print(f"{result:40} {lesson['title']}")
    print(f"left unjudged: {len(unjudged())} | cost about ${sum(llm.spend().values()) - spent0:.2f}")


if __name__ == "__main__":
    main()
