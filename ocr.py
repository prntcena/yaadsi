#!/usr/bin/env python3
"""OCR any stored slide image that has no OCR yet, with Muse Spark Open (vision).

Existing link-probe OCR (../link-probe/out/ocr/<sha>.json) is reused; new results go to
data/ocr/<sha>.json in the same shape ({"text", "description", "model"}).
"""
from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor

import llm
from corpus import PROBE, load_json

OCR_DIR = llm.DATA / "ocr"
OCR_SYSTEM = """Read this slide from a saved social-media post. Return JSON
{"text": str,          // all readable text on the slide, in reading order
 "description": str}   // one sentence on any diagram, chart or picture and what it shows"""
MIME = {"jpg": "image/jpeg", "png": "image/png", "webp": "image/webp"}


def ocr_paths(sha: str) -> list:
    return [PROBE / "out" / "ocr" / f"{sha}.json", OCR_DIR / f"{sha}.json"]


def missing_slides() -> list[dict]:
    slides = load_json(PROBE / "out" / "slides.json", {}).get("links", [])
    todo = {}
    for row in slides:
        for s in row.get("slides") or []:
            sha, key = s.get("sha256"), s.get("blob_key")
            if sha and key and not any(p.exists() for p in ocr_paths(sha)):
                todo[sha] = key
    return [{"sha": sha, "blob_key": key} for sha, key in todo.items()]


def ocr_one(item: dict) -> None:
    data = (PROBE / "blobs" / item["blob_key"]).read_bytes()
    mime = MIME.get(item["blob_key"].rsplit(".", 1)[-1], "image/jpeg")
    result = llm.chat_json([{"role": "system", "content": OCR_SYSTEM},
                            {"role": "user", "content": [llm.image_part(data, mime)]}], purpose="ocr")
    out = {"text": result.get("text", ""), "description": result.get("description", ""), "model": llm.BRAIN_MODEL}
    (OCR_DIR / f"{item['sha']}.json").write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")


def main() -> None:
    llm.guard_builders("ocr.py")
    OCR_DIR.mkdir(parents=True, exist_ok=True)
    todo = missing_slides()
    print(f"{len(todo)} slide images without OCR")
    with ThreadPoolExecutor(8) as pool:
        for item, err in zip(todo, pool.map(lambda i: _safe(ocr_one, i), todo)):
            print(f"  {item['sha'][:12]}: {'ok' if not err else err}")


def _safe(fn, item):
    try:
        fn(item)
        return ""
    except (llm.LLMError, ValueError, OSError) as exc:
        return f"failed: {exc}"


if __name__ == "__main__":
    main()
