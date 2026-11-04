"""Read-only access to the saves (corpus + studio analysis) and the agent's tools over them."""
from __future__ import annotations

import json
import re
from functools import lru_cache

import llm

READ_MAX_CHARS = 6000
ANALYSIS_KEYS = ("learnable", "title", "summary", "topics", "concepts", "difficulty", "minutes", "has_diagram")   # = studio.ANALYSIS_KEYS


@lru_cache(maxsize=1)
def saves() -> dict[str, dict]:
    """id -> corpus record merged with its studio analysis (title/summary/topics/concepts/...)."""
    corpus = json.loads((llm.DATA / "corpus.json").read_text(encoding="utf-8"))
    out = {}
    for record in corpus:
        path = llm.DATA / "studio" / f"{record['id']}.json"
        if path.exists():
            analysis = json.loads(path.read_text(encoding="utf-8"))
            # only the analysis schema's fields: model output must never override url, user_note, text...
            out[record["id"]] = {**record, **{k: analysis[k] for k in ANALYSIS_KEYS if k in analysis},
                                 "raw_title": record["title"]}
    return out


@lru_cache(maxsize=1)
def topic_map() -> dict[str, dict]:
    return json.loads((llm.DATA / "map.json").read_text(encoding="utf-8"))


def card(save_id: str) -> dict:
    """What the UI shows for a source: never the full text."""
    s = saves()[save_id]
    return {"id": save_id, "title": s.get("title") or s["raw_title"], "platform": s["platform"], "url": s["url"],
            "summary": s.get("summary", ""), "note": s.get("user_note", ""), "minutes": s.get("minutes", 0),
            "difficulty": s.get("difficulty", 3), "has_images": bool(s.get("images")),
            "kept": s["platform"] != "note" and len(s.get("text") or "") >= 200}   # Yaad holds real notes from the source


def topics_overview(top_concepts: int = 6) -> list[dict]:
    """Most concepts appear in one save only, so rank by how much their words recur across the topic."""
    out = []
    for name, entry in topic_map().items():
        freq: dict[str, int] = {}
        for concept, ids in entry["concepts"].items():
            for w in _words(concept):
                freq[w] = freq.get(w, 0) + len(ids)
        ranked = sorted(entry["concepts"].items(),
                        key=lambda kv: -(len(kv[1]) * 3 + sum(freq[w] for w in _words(kv[0])) / max(1, len(_words(kv[0])))))
        out.append({"name": name, "saves": len(entry["saves"]), "minutes": entry["minutes"],
                    "concepts": [c for c, _ in ranked[:top_concepts]]})
    return out


def _words(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]{3,}", text.lower()))


# Words that say nothing about the subject ("that reel about ...") would otherwise dominate keyword search.
STOPWORDS = {"the", "and", "for", "that", "this", "what", "was", "which", "who", "how", "why", "about", "with",
             "from", "saved", "save", "saves", "reel", "reels", "post", "posts", "video", "link", "links", "one",
             "some", "did", "does", "can", "you", "your", "there", "where", "when", "tell", "show", "me", "i"}


def relevance(query: str, save: dict) -> int:
    q = _words(query) - STOPWORDS
    hay = " ".join([save.get("title", ""), save.get("summary", ""), " ".join(save.get("concepts", [])),
                    " ".join(save.get("topics", [])), save.get("user_note", "")])
    return len(q & _words(hay)) * 2 + len(q & _words(save["text"][:3000]))


def search_saves(query: str, topic: str = "", limit: int = 5) -> list[dict]:
    allowed = set(topic_map().get(topic, {}).get("saves", [])) if topic else None
    scored = []
    for sid, s in saves().items():
        if allowed is not None and sid not in allowed:
            continue
        if score := relevance(query, s):
            scored.append((score, sid))
    return [{"id": sid, "title": saves()[sid].get("title", ""), "summary": saves()[sid].get("summary", "")}
            for _, sid in sorted(scored, reverse=True)[:limit]]


def read_save(save_id: str) -> dict:
    s = saves().get(save_id)
    if not s:
        return {"error": f"no save with id {save_id}"}
    return {"id": save_id, "title": s.get("title", ""), "platform": s["platform"], "your_note": s.get("user_note", ""),
            "summary": s.get("summary", ""), "concepts": s.get("concepts", []), "content": s["text"][:READ_MAX_CHARS]}


GENERIC = {"tools", "tool", "using", "based", "basics", "guide", "tips", "list", "free", "best", "design", "data",
           "ideas", "learning", "work", "make", "build", "your", "into", "with", "more", "online", "ways", "types"}


def _concept_words(save: dict) -> set[str]:
    return {w for c in save.get("concepts", []) for w in _words(c) if len(w) >= 4} - STOPWORDS - GENERIC


@lru_cache(maxsize=1)
def graph() -> dict:
    """Knowledge-brain data: topic nodes, save nodes, save->topic links and the strongest save<->save links
    (shared concept words, at most 2 per save). Built from map.json + studio data; no model calls."""
    topics, saves_out, links = [], {}, []
    all_saves = saves()
    for i, (name, entry) in enumerate(topic_map().items()):
        tid = f"t{i}"
        topics.append({"id": tid, "kind": "topic", "name": name, "saves": len(entry["saves"]),
                       "minutes": entry["minutes"], "color": i})
        for sid in entry["saves"]:
            s = all_saves.get(sid)
            if not s:
                continue
            saves_out.setdefault(sid, {"id": sid, "kind": "save", "title": (s.get("title") or s["raw_title"])[:80],
                                       "platform": s["platform"], "difficulty": s.get("difficulty", 3), "minutes": int(s.get("minutes") or 10),
                                       "note": s.get("user_note", "")[:80], "url": s.get("url", ""),
                                       "available": s.get("available", True) is not False,
                                       "learnable": s.get("learnable", True) is not False, "topics": []})["topics"].append(tid)
            links.append({"source": sid, "target": tid, "kind": "topic"})
    words = {sid: _concept_words(all_saves[sid]) for sid in saves_out}
    seen = set()
    for sid, w in words.items():
        best = sorted(((len(w & w2), other) for other, w2 in words.items() if other != sid and w & w2), reverse=True)[:2]
        for _, other in best:
            key = tuple(sorted((sid, other)))
            if key not in seen:
                seen.add(key)
                links.append({"source": sid, "target": other, "kind": "concept",
                              "shared": sorted(w & words[other])[:3]})
    return {"topics": topics, "saves": list(saves_out.values()), "links": links}


def related(save_ids: list[str], exclude: set[str], goal: str = "", n: int = 4, planned: set[str] = frozenset()) -> list[dict]:
    """Saves worth exploring next: shared concept words + same topic + goal relevance (no model calls)."""
    all_saves = saves()
    mine = [all_saves[i] for i in save_ids if i in all_saves]
    words = set().union(*[_concept_words(s) for s in mine]) if mine else set()
    topics = {tp for s in mine for tp in s.get("topics", [])}
    scored = []
    for sid, s in all_saves.items():
        if sid in exclude or sid in save_ids or not s.get("learnable", True):
            continue
        shared = sorted(words & _concept_words(s))
        same_topic = bool(topics & set(s.get("topics", [])))
        score = 3 * len(shared) + (2 if same_topic else 0) + min(relevance(goal, s), 4) if goal else 3 * len(shared) + (2 if same_topic else 0)
        score -= 4 if sid in planned else 0   # already coming up in the plan: discovery comes first
        if score > 0:
            phrases = [c for c in s.get("concepts", []) if _words(c) & set(shared)]   # show the real concept names
            why = (f"Also covers: {'; '.join(phrases[:2])}" if phrases else f"Same topic: {(s.get('topics') or [''])[0]}")
            scored.append((score, sid, why))
    scored.sort(reverse=True)
    return [{**card(sid), "why": why} for _, sid, why in scored[:n]]


_UNSAFE = re.compile(r"[\"`<>{}\[\]|;#&]")


def _label(text: str) -> str:
    return _UNSAFE.sub(" ", str(text)).strip()[:60] or "?"


def diagram_parts(diagram) -> dict:
    """Model output is untrusted: coerce the diagram to {nodes: [dict], edges: [list]}."""
    diagram = diagram if isinstance(diagram, dict) else {}
    nodes = diagram.get("nodes") if isinstance(diagram.get("nodes"), list) else []
    edges = diagram.get("edges") if isinstance(diagram.get("edges"), list) else []
    return {"nodes": [n for n in nodes if isinstance(n, dict)], "edges": edges}


def mermaid(diagram: dict) -> str:
    return mermaid_with_ids(diagram)[0]


def mermaid_with_ids(diagram: dict) -> tuple[str, dict[str, str]]:
    """Build Mermaid text from model data ({nodes:[{id,label}], edges:[[a,b,label?]]}); the model never writes Mermaid.
    Also returns model node id -> our safe id (n0, n1, ...), which the animation player uses."""
    diagram = diagram_parts(diagram)
    ids: dict[str, str] = {}
    lines = ["flowchart TD"]
    for i, node in enumerate((diagram or {}).get("nodes", [])[:14]):
        ids[str(node.get("id", i))] = f"n{i}"
        lines.append(f'  n{i}["{_label(node.get("label", ""))}"]')
    for edge in (diagram or {}).get("edges", [])[:20]:
        if not isinstance(edge, (list, tuple)) or len(edge) < 2:
            continue
        a, b = ids.get(str(edge[0])), ids.get(str(edge[1]))
        if a and b:
            label = _label(edge[2]) if len(edge) > 2 and edge[2] else ""
            lines.append(f'  {a} -->|"{label}"| {b}' if label else f"  {a} --> {b}")
    return ("\n".join(lines) if len(lines) > 1 else ""), ids


AGENT_TOOLS = [
    {"type": "function", "function": {
        "name": "read_save", "description": "Read one of the learner's saved items (full content, their note, concepts).",
        "parameters": {"type": "object", "properties": {"save_id": {"type": "string"}}, "required": ["save_id"]}}},
    {"type": "function", "function": {
        "name": "search_saves", "description": "Search the learner's other saves for related material.",
        "parameters": {"type": "object", "properties": {"query": {"type": "string"}, "topic": {"type": "string"}},
                       "required": ["query"]}}},
]


def run_tool(name: str, args: dict) -> dict | list:
    if name == "read_save":
        return read_save(str(args.get("save_id", "")))
    if name == "search_saves":
        return search_saves(str(args.get("query", "")), str(args.get("topic", "")))
    return {"error": f"unknown tool {name}"}
