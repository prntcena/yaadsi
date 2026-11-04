"""Visual explainer: the real figure from a saved carousel (SAM 3.1 crop) and a kid-simple picture
(Muse Image). Outputs are cached in data/media with content-addressed names; the app serves only those.
"""
from __future__ import annotations

import hashlib
import io
import json
import re

from PIL import Image

import llm
import tools
from corpus import PROBE, read_ocr, load_json

MEDIA = llm.DATA / "media"
MEDIA_NAME = re.compile(r"^(fig|pic)-[0-9a-f]{12,64}\.png$")
FIGURE_WORDS = ("diagram", "flowchart", "flow", "workflow", "chart", "architecture", "table", "graph", "map",
                "framework", "pipeline", "infographic", "tree")
SAM_QUERIES = ("diagram", "chart", "illustration")
MIN_BOX_SHARE = 0.12
PAD = 0.03
MIME = {"jpg": "image/jpeg", "png": "image/png", "webp": "image/webp"}
PICTURE_STYLE = ("Flat, friendly children's-book illustration, soft colours, simple shapes, minimal or no text, "
                 "one clear visual metaphor, white background.")


def _slides_for(save_id: str) -> list[dict]:
    rows = load_json(PROBE / "out" / "slides.json", {}).get("links", [])
    for row in rows:
        if row.get("shortcode") == save_id:
            return [s for s in row.get("slides") or [] if s.get("blob_key") and s.get("sha256")]
    return []


def pick_slide(save_ids: list[str]) -> tuple[str, dict] | None:
    """The slide most likely to hold a teachable figure, across the lesson's saves.
    Only slides whose OCR description names a figure qualify; Reel covers are photos, not figures."""
    best, best_score = None, -1.0
    for sid in save_ids:
        slides = _slides_for(sid)
        for s in slides:
            if str(s.get("is_video")) == "True":
                continue
            ocr = read_ocr(s["sha256"])
            desc = (ocr.get("description") or "").lower()
            figure_hits = sum(bool(re.search(rf"\b{w}s?\b", desc)) for w in FIGURE_WORDS)
            if not figure_hits:
                continue
            text_len = len((ocr.get("text") or "").split())
            score = 10.0 * figure_hits + min(text_len, 80) / 20
            if str(s.get("index")) == "1" and len(slides) > 1:
                score -= 3                     # cover slides are usually a hook, not the content
            if score > best_score:
                best, best_score = (sid, s), score
    return best


def crop_figure(save_id: str, slide: dict) -> dict:
    sha = slide["sha256"]
    name = f"fig-{sha[:16]}.png"
    out = MEDIA / name
    meta = MEDIA / f"fig-{sha[:16]}.json"     # remembers whether SAM found a figure box
    box = None
    if out.exists() and meta.exists():
        box = json.loads(meta.read_text(encoding="utf-8")).get("box")
    else:
        data = (PROBE / "blobs" / slide["blob_key"]).read_bytes()
        img = Image.open(io.BytesIO(data)).convert("RGB")
        w, h = img.size
        mime = MIME.get(slide["blob_key"].rsplit(".", 1)[-1], "image/jpeg")
        for query in SAM_QUERIES:
            boxes = llm.segment(data, mime, query, purpose="visuals:sam")
            boxes = [b for b in boxes if (b[2] - b[0]) * (b[3] - b[1]) >= MIN_BOX_SHARE * w * h]
            if boxes:
                box = max(boxes, key=lambda b: (b[2] - b[0]) * (b[3] - b[1]))
                break
        if box:
            px, py = int(w * PAD), int(h * PAD)
            box = (max(0, box[0] - px), max(0, box[1] - py), min(w, box[2] + px), min(h, box[3] + py))
            img = img.crop(box)
        MEDIA.mkdir(parents=True, exist_ok=True)
        img.save(out, "PNG")
        meta.write_text(json.dumps({"box": list(box) if box else None}), encoding="utf-8")
    full = f"full-{sha[:16]}"
    return {"url": f"/media/{name}", "save_id": save_id, "slide_index": slide.get("index"),
            "cropped": box is not None, "full_slide": full, "title": tools.saves().get(save_id, {}).get("title", "")}


def figure_for(save_ids: list[str]) -> dict | None:
    picked = pick_slide(save_ids)
    return crop_figure(*picked) if picked else None


def picture_prompt(lesson: dict) -> str:
    first = (lesson.get("sections") or [{}])[0]
    idea = " ".join(str(first.get("body", "")).split()[:60])
    return f"{PICTURE_STYLE} Topic: {lesson.get('title', '')}. Show this idea: {idea}"


def picture_for(lesson: dict) -> dict:
    prompt = picture_prompt(lesson)
    name = f"pic-{hashlib.sha256(prompt.encode('utf-8')).hexdigest()[:24]}.png"
    out = MEDIA / name
    if not out.exists():
        data = llm.generate_image(prompt, purpose="visuals:picture")
        MEDIA.mkdir(parents=True, exist_ok=True)
        Image.open(io.BytesIO(data)).convert("RGB").save(out, "PNG")
    return {"url": f"/media/{name}"}


def full_slide_path(token: str):
    """Resolve full-<sha16> to the original slide image (served for 'see the full slide')."""
    m = re.fullmatch(r"full-([0-9a-f]{16})", token)
    if not m:
        return None
    for row in load_json(PROBE / "out" / "slides.json", {}).get("links", []):
        for s in row.get("slides") or []:
            if (s.get("sha256") or "").startswith(m.group(1)) and s.get("blob_key"):
                path = PROBE / "blobs" / s["blob_key"]
                return path if path.exists() else None
    return None
