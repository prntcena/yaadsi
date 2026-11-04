#!/usr/bin/env python3
"""Build data/corpus.json from the link-probe outputs: one record per saved link or long text note.

Sources (all under ../link-probe): messages.txt (the founder's self-chat, for user notes and text-only
notes), out/results.json (fetched page text), out/slides.json + out/ocr/<sha>.json (carousel captions
and slide OCR), out/reel-briefs.json (Reel title/brief), blobs/ (slide images).
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

import llm

HERE = Path(__file__).resolve().parent
PROBE = HERE.parent / "link-probe"
DATA = HERE / (os.environ.get("TUTOR_DATA") or "data")   # same rule as llm.DATA

URL_RE = re.compile(r"https?://[^\s<>\"']+", re.I)
WA_PREFIX = re.compile(r"^\[[^\]]{5,40}\]\s*[^:]{1,60}:\s?", re.M)   # "[1:40 am, 12/11/2025] Name: "
IG_CODE = re.compile(r"instagram\.com/(?:[\w.]+/)?(?:p|reels?|tv)/([A-Za-z0-9_-]+)")
MIN_NOTE_WORDS = 25          # shorter text-only messages are labels, not notes
MAX_TEXT_CHARS = 12000


def load_json(path: Path, default):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def canonical_key(url: str) -> str:
    if m := IG_CODE.search(url):
        return "ig:" + m.group(1)
    return re.sub(r"[?#].*$", "", url.lower()).rstrip("/")


def user_notes(raw: str) -> tuple[dict[str, str], list[str]]:
    """Map link key -> the founder's own words next to it; collect long text-only notes.

    The chat is split into blank-line blocks (WhatsApp prefixes stripped). Short text in the same
    block as a link, or in the short block right after it, is that link's note ("Hooks", "Dsa prompts").
    """
    notes: dict[str, str] = {}
    text_notes: list[str] = []
    last_urls: list[str] = []
    buffer: list[str] = []          # consecutive text-only blocks form one note

    def flush() -> None:
        text = "\n".join(buffer).strip()
        if len(text.split()) >= MIN_NOTE_WORDS:
            text_notes.append(text)
        buffer.clear()

    units = (msg for block in re.split(r"\n\s*\n", raw) for msg in WA_PREFIX.split(block))
    for block in units:
        block = block.strip()
        if not block:
            continue
        urls = [u.rstrip(".,)") for u in URL_RE.findall(block)]
        rest = re.sub(r"\s*\n\s*", "\n", URL_RE.sub(" ", block)).strip()
        if urls:
            flush()
            last_urls = urls
            if rest and len(rest.split()) <= 60:
                for u in urls:
                    notes.setdefault(canonical_key(u), rest)
        elif last_urls and len(rest.split()) <= 12:
            for u in last_urls:
                notes.setdefault(canonical_key(u), rest)
            last_urls = []
        else:
            buffer.append(rest)
            last_urls = []
    flush()
    return notes, text_notes


def read_ocr(sha: str) -> dict:
    """link-probe's OCR first, then tutor-lab's own (ocr.py fills gaps with Muse Spark Open)."""
    for path in (PROBE / "out" / "ocr" / f"{sha}.json", DATA / "ocr" / f"{sha}.json"):
        if path.exists():
            return load_json(path, {})
    return {}


def slide_text(slides: list[dict]) -> tuple[str, list[str]]:
    texts, images = [], []
    for s in slides:
        sha = s.get("sha256") or ""
        if s.get("blob_key"):
            images.append(s["blob_key"])
        ocr = read_ocr(sha) if sha else {}
        piece = " ".join(filter(None, [ocr.get("text", ""), ocr.get("description", "")])).strip()
        if piece:
            texts.append(f"[slide {s.get('index')}] {piece}")
    return "\n".join(texts), images


def build() -> list[dict]:
    raw = (PROBE / "messages.txt").read_text(encoding="utf-8")
    notes, text_notes = user_notes(raw)
    results = load_json(PROBE / "out" / "results.json", {}).get("links", [])
    slides = {row["shortcode"]: row for row in load_json(PROBE / "out" / "slides.json", {}).get("links", [])}
    briefs = load_json(PROBE / "out" / "reel-briefs.json", {})

    corpus: list[dict] = []
    for link in results:
        url = link.get("canonical") or link.get("url", "")
        key = canonical_key(url)
        code = link.get("ident") if link.get("platform", "").startswith("instagram") else ""
        parts = [link.get("title", ""), link.get("text", "")]
        images: list[str] = []
        if code and code in slides:
            row = slides[code]
            parts.append(row.get("caption", ""))
            ocr_text, images = slide_text(row.get("slides") or [])
            parts.append(ocr_text)
        if code and code in briefs:
            b = briefs[code]
            parts.append(f"Reel: {b.get('title', '')}\n{b.get('brief', '')}")
        text = "\n\n".join(p.strip() for p in parts if p and p.strip())[:MAX_TEXT_CHARS]
        corpus.append({
            "id": code or re.sub(r"\W+", "-", key)[-60:].strip("-"),
            "kind": "link", "platform": link.get("platform", ""), "url": url,
            "title": (link.get("title") or (briefs.get(code, {}) or {}).get("title") or "")[:200],
            "user_note": notes.get(key, ""), "text": text, "images": images,
            "available": bool(text.strip()), "outcome": link.get("outcome", ""),
        })
    for i, note in enumerate(text_notes, 1):
        corpus.append({"id": f"note-{i}", "kind": "note", "platform": "note", "url": "",
                       "title": note.splitlines()[0][:120], "user_note": "", "text": note[:MAX_TEXT_CHARS],
                       "images": [], "available": True, "outcome": "NOTE"})
    seen = {c["id"] for c in corpus}   # links and notes added by hand in the app (ingest.py) survive a rebuild
    for r in load_json(DATA / "added.json", []):
        if r.get("id") and r["id"] not in seen:
            seen.add(r["id"])
            corpus.append(r)
    return corpus


def main() -> None:
    llm.guard_builders("corpus.py")
    corpus = build()
    DATA.mkdir(exist_ok=True)
    (DATA / "corpus.json").write_text(json.dumps(corpus, indent=2, ensure_ascii=False), encoding="utf-8")
    usable = [c for c in corpus if c["available"]]
    by_platform: dict[str, int] = {}
    for c in usable:
        by_platform[c["platform"]] = by_platform.get(c["platform"], 0) + 1
    print(f"{len(corpus)} records, {len(usable)} with text; by platform: {by_platform}")
    print(f"with user notes: {sum(1 for c in corpus if c['user_note'])}; "
          f"with images: {sum(1 for c in corpus if c['images'])}")


if __name__ == "__main__":
    main()
