"""Live "Yaad is thinking" lines: short snippets of Spark Open's streamed reasoning plus step events.

A Channel belongs to one request. llm.chat streams into the channel of the current thread (set with use()).
Reasoning text is untrusted model output: it is trimmed, filtered, throttled and never logged or stored.
Most low-effort reasoning is about the output format, so a snippet is shown only if it mentions the material
being taught (focus()) - an allowlist - and the blocklist below catches the rest."""
from __future__ import annotations

import re
import threading
import time
from contextlib import contextmanager

EVERY = 1.2          # seconds between reasoning snippets
MAX_CHARS = 140
# The model's talk about its instructions or output format, not about the learner's saves.
META = re.compile(r"\b(comply|instructions?|system prompt|the prompt|json|schema|format|policy|field|output|"
                  r"respond|reply|word limit|words?|sentences?|sections?|headings?|bullets?|lists?|markdown|tone|"
                  r"contractions?|contracting|avoid|quiz|options?|answer_index|minutes|sources|we (need|must|should|have)|"
                  r"let me|i (need|must|should)|need to|must be|user wants|the user|the assistant|tool call|speak|"
                  r"level|answerable|follow.?up|e\.?g)\b", re.I)
COUNTING = re.compile(r"\b[A-Za-z]+\d+\b")   # the model counting words ("your23 room24")
FILLER = re.compile(r"^(Draft|Answer|Maybe|So|Okay|Ok|Now)\b[:,]?\s*")
_SENTENCE = re.compile(r"(?<=[.!?])\s+")
_WORD = re.compile(r"[a-z]{5,}")
GENERIC = {"about", "their", "there", "these", "those", "which", "would", "could", "should", "using", "saved", "saves",
           "lesson", "learner", "explain", "simple", "example", "things", "first", "maybe", "probably", "really"}


def words(text: str) -> set[str]:
    return set(_WORD.findall(text.lower())) - GENERIC


class Channel:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._out: list[str] = []
        self._buf = ""
        self._last_emit = 0.0
        self._last = ""
        self.vocab: set[str] = set()   # words of the material being taught; a thought must mention one

    def put_step(self, text: str) -> None:
        self._emit(text[:MAX_CHARS])

    def feed_reasoning(self, delta: str) -> None:
        with self._lock:
            self._buf = (self._buf + delta)[-1500:]
            due = time.time() - self._last_emit >= EVERY
            buf = self._buf
        if not due:
            return
        for sentence in reversed(_SENTENCE.split(buf)[:-1]):   # complete sentences only, newest first
            if snippet := _clean(sentence, self.vocab):
                self._emit(snippet)
                return

    def _emit(self, text: str) -> None:
        with self._lock:
            if text and text != self._last:
                self._out.append(text)
                self._last, self._last_emit = text, time.time()

    def drain(self) -> list[str]:
        with self._lock:
            out, self._out = self._out, []
            return out


def _clean(sentence: str, vocab: set[str] | None = None) -> str:
    s = re.sub(r"[`*#_>\[\]{}\"]", "", " ".join(sentence.split())).strip(" -:")
    s = FILLER.sub("", s).strip()
    if len(s) < 15 or len(s.split()) < 5 or "?" in s or META.search(s) or COUNTING.search(s) \
            or re.search(r"[A-Za-z0-9_-]{18,}", s) or _has_save_id(s):   # meta talk, counting, musings, raw ids
        return ""
    if vocab and not words(s) & vocab:   # allowlist: it must be about the material, not about the format
        return ""
    s = re.sub(r"\b[Tt]he learner\b", "you", re.sub(r"\b[Tt]he learner's\b", "your", s))
    s = s[0].upper() + s[1:]
    return s if len(s) <= MAX_CHARS else s[:MAX_CHARS - 1].rsplit(" ", 1)[0] + "…"


def _has_save_id(text: str) -> bool:
    import tools   # lazy: tools -> llm -> thoughts
    ids = tools.saves()
    return any(tok.strip(".,;:()") in ids for tok in text.split())


_local = threading.local()


def current() -> Channel | None:
    return getattr(_local, "channel", None)


@contextmanager
def use(channel: Channel | None):
    previous = current()
    _local.channel = channel
    try:
        yield channel
    finally:
        _local.channel = previous


def focus(text: str) -> None:
    """Tell the current channel what is being taught, so only thoughts about it are shown."""
    if ch := current():
        ch.vocab |= words(text)


def step(text: str) -> None:
    """A progress event ("Reading your save…") for the current request, if anyone is listening."""
    if ch := current():
        ch.put_step(text)
