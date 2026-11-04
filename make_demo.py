#!/usr/bin/env python3
"""Build the public demo corpus (demo/) from the founder's data/: 24 public saves picked as safe to show.

    python make_demo.py            # writes demo/corpus.json, demo/map.json, demo/studio/<id>.json
    python make_demo.py --check    # before a push: only approved saves, no personal notes, no learner rules

Only public posts by other creators on learning topics. Personal notes are blanked, and each save keeps a short
excerpt of its text (EXCERPT chars) plus Yaad's own analysis, not the creator's full post. Then run the app with
TUTOR_DATA=demo; lessons, answers and Connect the dots made on demo/ are cached there and replay without keys.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRC, DST = HERE / "data", HERE / "demo"
EXCERPT = 1500
MIN_TOPIC_SAVES = 2

DEMO_IDS = [
    # software engineering & systems
    "DV02icRE1Z0", "linkedin-com-feed-update-urn-li-activity-7405595393402556416",
    "linkedin-com-feed-update-urn-li-activity-7408755754494607360", "DY3rLu6FMm6", "DY_OCbUkuA0", "DUaCif-kfOt",
    "gle-com-architecture-choose-design-pattern-agentic-ai-system", "https-google-github-io-adk-docs",
    "https-github-com-usrbinkat-obelisk",
    # AI & machine learning
    "DONQZcYE90_", "https-github-com-premai-io-state-of-open-source-ai", "https-github-com-dvgodoy-finetuningllms",
    "linkedin-com-feed-update-urn-li-activity-7428320533613780992",
    "linkedin-com-feed-update-urn-li-activity-7421516454489088000",
    # career & interview prep
    "DTh_CT8k_gj", "DTg1Dnqk8tY", "ww-linkedin-com-feed-update-urn-li-share-7516022880535527424", "DUWMqQfCMCb",
    # design & content
    "DPbgEw8CBgv", "DRcqa3UkTuo", "DSNmIOgAYQ2", "DQr2EaKDDFn",
    # already gone from Instagram (URL only)
    "DQ65dbPjIRw", "DMQD0dUzn7X",
]


def _read(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _write(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def build() -> dict:
    keep = set(DEMO_IDS)
    corpus = [r for r in _read(SRC / "corpus.json") if r["id"] in keep]
    missing = keep - {r["id"] for r in corpus}
    if missing:
        raise SystemExit(f"not in data/corpus.json: {sorted(missing)}")
    for r in corpus:
        r["user_note"] = ""
        r["text"] = (r.get("text") or "")[:EXCERPT] if r.get("available", True) else ""

    if (DST / "studio").exists():   # replace the source files only; the pre-made lessons and answers stay
        shutil.rmtree(DST / "studio")
    _write(DST / "corpus.json", corpus)
    minutes = {}
    for r in corpus:
        if (path := SRC / "studio" / f"{r['id']}.json").exists():
            analysis = _read(path)
            minutes[r["id"]] = int(analysis.get("minutes") or 10)
            _write(DST / "studio" / path.name, analysis)

    topics = {}
    for name, entry in _read(SRC / "map.json").items():
        saves = [s for s in entry["saves"] if s in keep]
        if len(saves) < MIN_TOPIC_SAVES:
            continue
        concepts = {c: [s for s in ids if s in keep] for c, ids in entry.get("concepts", {}).items()}
        topics[name] = {**entry, "saves": saves, "concepts": {c: ids for c, ids in concepts.items() if ids},
                        "minutes": sum(minutes.get(s, 10) for s in saves)}
    _write(DST / "map.json", topics)
    return {"saves": len(corpus), "gone": sum(1 for r in corpus if not r.get("available", True)),
            "topics": {name: len(t["saves"]) for name, t in topics.items()}}


def check() -> list[str]:
    """Problems that must block a push: anything in demo/ beyond the approved saves, or personal notes."""
    keep, problems = set(DEMO_IDS), []
    corpus = _read(DST / "corpus.json")
    problems += [f"not an approved save: {r['id']}" for r in corpus if r["id"] not in keep]
    problems += [f"personal note kept: {r['id']}" for r in corpus if r.get("user_note")]
    problems += [f"text longer than the excerpt: {r['id']}" for r in corpus if len(r.get("text") or "") > EXCERPT]
    problems += [f"studio file for an unapproved save: {p.name}" for p in (DST / "studio").glob("*.json") if p.stem not in keep]
    problems += [f"map lists an unapproved save: {s}" for t in _read(DST / "map.json").values() for s in t["saves"] if s not in keep]
    problems += [f"learner rules in a lesson: {p.name}" for p in (DST / "lessons").glob("*.json") if _read(p).get("rules_used")]
    return problems   # demo/profile.json and demo/added.json are learner state: .gitignore keeps them local


if __name__ == "__main__":
    import sys
    if sys.argv[1:] == ["--check"]:
        issues = check()
        print("\n".join(issues) or "demo/ is clean")
        sys.exit(1 if issues else 0)
    print(json.dumps(build(), indent=2))
