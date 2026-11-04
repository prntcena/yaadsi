"""The learner's whole brain as one stable text block (~55k tokens), so Muse Spark Open can read everything they saved
in a single call. Byte-identical across calls (sorted, deterministic) so Baseten's cached-input pricing applies:
always put this block FIRST in the messages and the variable question last."""
from __future__ import annotations

import hashlib
import threading

import llm
import tools

MAX_CONTENT = 4000
_lock = threading.Lock()
_cache: dict = {"stamp": None, "text": "", "hash": ""}


def _stamp() -> tuple:
    studio = llm.DATA / "studio"
    files = [llm.DATA / "corpus.json", llm.DATA / "map.json", *sorted(studio.glob("*.json"))]
    return tuple(round(f.stat().st_mtime, 3) for f in files if f.exists())


def whole_brain() -> tuple[str, str]:
    """(text, hash) of every learnable save, rebuilt only when the corpus or studio data change."""
    with _lock:
        stamp = _stamp()
        if _cache["stamp"] != stamp:
            tools.saves.cache_clear(); tools.topic_map.cache_clear()
            parts = []
            for sid, s in sorted(tools.saves().items()):
                if not s.get("learnable", True) or not s.get("text"):
                    continue
                lines = [f"[{sid}] {s['platform']} · {s.get('title') or s['raw_title']}"]
                if s.get("user_note"):
                    lines.append(f"your note: {s['user_note']}")
                if s.get("summary"):
                    lines.append(f"summary: {s['summary']}")
                lines.append(f"content: {' '.join(s['text'].split())[:MAX_CONTENT]}")
                parts.append("\n".join(lines))
            text = "THE LEARNER'S SAVED LINKS (their whole brain):\n\n" + "\n\n---\n\n".join(parts)
            _cache.update(stamp=stamp, text=text, hash=hashlib.sha256(text.encode("utf-8")).hexdigest()[:12])
        return _cache["text"], _cache["hash"]


def prefix() -> dict:
    """The first message of every whole-brain call. Same bytes for Ask, Connect the dots and anything else, so
    they all share one cached prefix on Baseten (the cache only matches from the very start of the prompt)."""
    return {"role": "system", "content": whole_brain()[0]}
