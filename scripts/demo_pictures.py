"""Draw the Muse Image picture for every demo lesson, so "Simple picture" opens instantly in replay mode.

    TUTOR_DATA=demo python scripts/demo_pictures.py    # META_API_KEY in .env or the environment

Run it after rejudge_demo_lessons.py: the picture's prompt comes from the lesson text, so a re-made lesson needs a new one.
Pictures already drawn are skipped.
"""
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import llm                      # noqa: E402
import visuals                  # noqa: E402
from lesson import CACHE        # noqa: E402


def draw(path: Path) -> str:
    lesson = json.loads(path.read_text(encoding="utf-8"))
    try:
        return f"ok      {visuals.picture_for(lesson)['url']}  {lesson['title']}"
    except (llm.LLMError, OSError, ValueError) as exc:
        return f"failed  {type(exc).__name__}  {lesson['title']}"


def main() -> None:
    if llm.DATA.name != "demo":
        sys.exit("run with TUTOR_DATA=demo")
    if not llm.has_keys():
        sys.exit("needs the keys in .env")
    with ThreadPoolExecutor(4) as pool:
        results = list(pool.map(draw, sorted(CACHE.glob("*.json"))))
    print("\n".join(results))
    if any(r.startswith("failed") for r in results):
        sys.exit(1)


if __name__ == "__main__":
    main()
