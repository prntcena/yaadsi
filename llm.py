"""Model clients for the tutor lab.

brain:  Muse Spark Open on Baseten (falls back to muse-spark-1.3 on the Meta API).
meta:   Muse Wonder (judge), Muse Image, SAM 3.1, Voice Transcribe on the Meta Model API.
Every call appends one row to out/usage.jsonl. Keys and raw SDK error bodies are never printed.
"""
from __future__ import annotations

import base64
import json
import os
import re
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import openai
from dotenv import dotenv_values

import thoughts

HERE = Path(__file__).resolve().parent
OUT = HERE / "out"
DATA = HERE / (os.environ.get("TUTOR_DATA") or "data")   # TUTOR_DATA=demo runs on the public demo corpus

BASETEN_BASE_URL = "https://inference.baseten.co/v1"
META_BASE_URL = "https://api.meta.ai/v1"
BRAIN_MODEL = "meta-models/muse-spark-open-1026"   # the hackathon key only works with this id
FALLBACK_BRAIN = "muse-spark-1.3"
JUDGE_MODEL = "muse-wonder-1.0"
IMAGE_MODEL = "muse-image-1.0"
SAM_MODEL = "sam-3.1"
TRANSCRIBE_MODEL = "muse-voice-transcribe-1.0"

# USD per million tokens; estimates only, the provider consoles are the truth.
PRICES = {BRAIN_MODEL: (1.25, 4.25), FALLBACK_BRAIN: (1.25, 4.25), JUDGE_MODEL: (1.25, 4.25)}
CACHED_PRICES = {BRAIN_MODEL: 0.15}   # Baseten cached-input price for Spark Open (USD / M tokens)

INTERACTIVE_MAX_TOKENS = 3000   # a ceiling for reasoning + answer, not a length control
STUDIO_MAX_TOKENS = 8000


class LLMError(RuntimeError):
    pass


class NoKey(LLMError):
    """No API key: the app runs in replay mode (cached lessons, answers and masterclasses only)."""


REPLAY_MSG = "Replay mode: this one isn't pre-made. Add your Meta and Baseten keys to .env to make it live."


def guard_builders(name: str) -> None:
    """The builders read the founder's link-probe data: never let them write into the public demo corpus."""
    if DATA.resolve() != (HERE / "data").resolve():
        raise SystemExit(f"{name} builds data/ only; unset TUTOR_DATA (it is {DATA.name}). Use make_demo.py for demo/.")


def has_keys() -> bool:
    return bool(setting("BASETEN_API_KEY") or setting("META_API_KEY"))


def setting(name: str, default: str = "") -> str:
    for env_path in (HERE / ".env", HERE.parent / "link-probe" / ".env"):
        if env_path.exists() and (value := (dotenv_values(env_path).get(name) or "").strip()):
            return value
    return os.environ.get(name, "").strip() or default


def write_json(path: Path, value) -> None:
    """Write a cache file atomically: readers see the old file or the whole new one, never half of it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(f"{path.suffix}.{threading.get_ident()}.tmp")   # one temp per writer thread
    tmp.write_text(json.dumps(value, indent=2, ensure_ascii=False), encoding="utf-8")
    for attempt in range(5):   # Windows refuses the replace while a reader has the file open; that lasts milliseconds
        try:
            tmp.replace(path)
            return
        except PermissionError:
            if attempt == 4:
                tmp.unlink(missing_ok=True)
                raise
            time.sleep(0.05 * (attempt + 1))


def api_error(exc: Exception) -> str:
    return f"{type(exc).__name__} {getattr(exc, 'status_code', '') or ''}".strip()


_clients: dict[str, openai.OpenAI] = {}


def _client(which: str) -> openai.OpenAI:
    if which not in _clients:
        key_name, base_url = {"baseten": ("BASETEN_API_KEY", BASETEN_BASE_URL),
                              "meta": ("META_API_KEY", META_BASE_URL)}[which]
        key = setting(key_name)
        if not key:
            raise NoKey(f"{key_name} is not set in tutor-lab/.env")
        _clients[which] = openai.OpenAI(api_key=key, base_url=base_url, timeout=180, max_retries=2)
    return _clients[which]


def _log(model: str, purpose: str, usage: Any, seconds: float, ok: bool = True, note: str = "") -> None:
    OUT.mkdir(exist_ok=True)
    tin = getattr(usage, "prompt_tokens", 0) or 0 if usage else 0
    tout = getattr(usage, "completion_tokens", 0) or 0 if usage else 0
    details = getattr(usage, "prompt_tokens_details", None) if usage else None
    cached = (getattr(details, "cached_tokens", 0) or 0) if details else 0
    pin, pout = PRICES.get(model, (0.0, 0.0))
    pcached = CACHED_PRICES.get(model, pin)
    row = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "model": model, "purpose": purpose, "ok": ok,
           "in": tin, "cached": cached, "out": tout,
           "usd": round(((tin - cached) * pin + cached * pcached + tout * pout) / 1e6, 6),
           "seconds": round(seconds, 2), "note": note}
    with _log_lock, (OUT / "usage.jsonl").open("a", encoding="utf-8") as f:   # pre-warm logs from many threads
        f.write(json.dumps(row) + "\n")


_log_lock = threading.Lock()


def spend() -> dict[str, float]:
    totals: dict[str, float] = {}
    path = OUT / "usage.jsonl"
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(line)
            except ValueError:
                continue   # a torn line must not break the spend view
            totals[row["model"]] = totals.get(row["model"], 0.0) + row["usd"]
    return totals


def image_part(data: bytes, mime: str = "image/jpeg") -> dict:
    return {"type": "image_url",
            "image_url": {"url": f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"}}


def chat(messages: list[dict], *, purpose: str, model: str = BRAIN_MODEL, json_mode: bool = False,
         tools: list[dict] | None = None, effort: str | None = "low",
         max_tokens: int = INTERACTIVE_MAX_TOKENS, temperature: float | None = None) -> Any:
    """One chat completion. Returns the message object. Brain calls fall back to Spark 1.3 on Meta.

    max_tokens covers reasoning plus the answer, so a cut-off answer is retried once with a
    higher ceiling instead of being returned empty."""
    which = "baseten" if model == BRAIN_MODEL else "meta"
    kwargs: dict[str, Any] = {"model": model, "messages": messages, "max_tokens": max_tokens}
    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}
    if tools:
        kwargs["tools"] = tools
    if effort:
        kwargs["reasoning_effort"] = effort
    if temperature is not None:
        kwargs["temperature"] = temperature
    channel = thoughts.current() if model == BRAIN_MODEL else None
    for attempt in range(2):
        started = time.time()
        if channel:   # someone is watching: stream so Yaad can show live thoughts
            try:
                message, usage, finish = _stream(kwargs, channel)
            except openai.APIStatusError as exc:   # the provider said no (429/5xx): retrying it plain only doubles the wait
                _log(model, purpose, None, time.time() - started, ok=False, note=f"stream {api_error(exc)}")
                return _fallback(exc, messages, purpose, json_mode, tools, effort, max_tokens, temperature)
            except Exception as exc:   # a dropped connection or a missing key: the plain call below decides
                if not isinstance(exc, NoKey):
                    _log(model, purpose, None, time.time() - started, ok=False, note=f"stream {type(exc).__name__}")
                channel = None   # retry this attempt without streaming (and the usual fallback after that)
            else:
                _log(model, purpose, usage, time.time() - started, note=finish)
                if finish == "length" and attempt == 0:
                    kwargs["max_tokens"] = max_tokens * 2
                    continue
                return message
        try:
            resp = _client(which).chat.completions.create(**kwargs)
        except (openai.APIError, NoKey) as exc:
            if not isinstance(exc, NoKey):
                _log(model, purpose, None, time.time() - started, ok=False, note=api_error(exc))
            elif model != BRAIN_MODEL:
                raise
            if model == BRAIN_MODEL:
                return _fallback(exc, messages, purpose, json_mode, tools, effort, max_tokens, temperature)
            raise LLMError(f"{model} failed: {api_error(exc)}") from None
        if not resp.choices:
            _log(model, purpose, resp.usage, time.time() - started, ok=False, note="no choices")
            raise LLMError(f"{model} returned no answer")
        choice = resp.choices[0]
        _log(model, purpose, resp.usage, time.time() - started, note=choice.finish_reason or "")
        if choice.finish_reason == "length" and attempt == 0:
            kwargs["max_tokens"] = max_tokens * 2
            continue
        return choice.message
    return choice.message


def _fallback(cause: Exception, messages, purpose, json_mode, tools, effort, max_tokens, temperature):
    """Spark Open failed (or has no key): try Muse Spark on the Meta API. Replay mode only when neither key is set."""
    try:
        return chat(messages, purpose=purpose + ":fallback", model=FALLBACK_BRAIN, json_mode=json_mode,
                    tools=tools, effort=effort, max_tokens=max_tokens, temperature=temperature)
    except NoKey:
        if isinstance(cause, NoKey):
            raise
        raise LLMError(f"{BRAIN_MODEL} failed: {api_error(cause)}") from None


def _stream(kwargs: dict, channel) -> tuple[Any, Any, str]:
    """A streamed Spark Open call, rebuilt into the same shape as a non-streamed message (content + tool_calls).
    Reasoning deltas go to the thought channel only; they are never returned, logged or stored."""
    stream = _client("baseten").chat.completions.create(**kwargs, stream=True, stream_options={"include_usage": True})
    content, calls, usage, finish = [], {}, None, ""
    for chunk in stream:
        if chunk.usage:
            usage = chunk.usage
        if not chunk.choices:
            continue
        choice = chunk.choices[0]
        delta = choice.delta
        finish = choice.finish_reason or finish
        reasoning = getattr(delta, "reasoning_content", None) or (getattr(delta, "model_extra", None) or {}).get("reasoning")
        if reasoning:
            channel.feed_reasoning(reasoning)
        if delta.content:
            content.append(delta.content)
        for tc in delta.tool_calls or []:
            slot = calls.setdefault(tc.index, {"id": "", "name": "", "arguments": ""})
            slot["id"] = tc.id or slot["id"]
            if tc.function:
                slot["name"] = tc.function.name or slot["name"]
                slot["arguments"] += tc.function.arguments or ""
    tool_calls = [SimpleNamespace(id=c["id"], type="function", function=SimpleNamespace(name=c["name"], arguments=c["arguments"]))
                  for _, c in sorted(calls.items())] or None
    return SimpleNamespace(role="assistant", content="".join(content), tool_calls=tool_calls), usage, finish


def chat_json(messages: list[dict], *, purpose: str, **kw: Any) -> dict:
    """Chat in JSON mode; one repair attempt if the reply does not parse."""
    msg = chat(messages, purpose=purpose, json_mode=True, **kw)
    try:
        return parse_json(msg.content)
    except ValueError:
        fixed = chat(messages + [{"role": "assistant", "content": msg.content or ""},
                                 {"role": "user", "content": "That was not valid JSON. Reply with only the JSON object."}],
                     purpose=purpose + ":repair", json_mode=True, **kw)
        return parse_json(fixed.content)


def parse_json(text: str | None) -> dict:
    text = (text or "").strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError("reply is not JSON") from exc
    if not isinstance(value, dict):
        raise ValueError("reply is not a JSON object")
    return value


def generate_image(prompt: str, *, purpose: str) -> bytes:
    started = time.time()
    try:
        resp = _client("meta").images.generate(model=IMAGE_MODEL, prompt=prompt, n=1)
    except openai.APIError as exc:
        _log(IMAGE_MODEL, purpose, None, time.time() - started, ok=False, note=api_error(exc))
        raise LLMError(f"image generation failed: {api_error(exc)}") from None
    _log(IMAGE_MODEL, purpose, None, time.time() - started)
    data = resp.data[0].b64_json
    if not data:
        raise LLMError("image generation returned no image data")
    return base64.b64decode(data)


_BOX = re.compile(r"<\|box;x1=(\d+);y1=(\d+);x2=(\d+);y2=(\d+);w=(\d+);h=(\d+)\|>")


def segment(image: bytes, mime: str, query: str, *, purpose: str) -> list[tuple[int, int, int, int]]:
    """SAM 3.1 boxes (x1, y1, x2, y2) in image pixels for `query`. Only /v1/responses serves SAM."""
    started = time.time()
    try:
        resp = _client("meta").responses.create(model=SAM_MODEL, input=[{"role": "user", "content": [
            {"type": "input_text", "text": query},
            {"type": "input_image", "image_url": f"data:{mime};base64,{base64.b64encode(image).decode('ascii')}"}]}])
    except openai.APIError as exc:
        _log(SAM_MODEL, purpose, None, time.time() - started, ok=False, note=api_error(exc))
        raise LLMError(f"segmentation failed: {api_error(exc)}") from None
    _log(SAM_MODEL, purpose, None, time.time() - started)
    text = "".join(part.text for item in resp.output for part in getattr(item, "content", []) or []
                   if getattr(part, "text", None))
    return [tuple(int(v) for v in m.groups()[:4]) for m in _BOX.finditer(text)]


ASR_URL = "https://api.meta.ai/v1/asr/transcribe"   # Muse Voice Transcribe is not on /audio/transcriptions


def transcribe(wav: bytes, *, purpose: str) -> str:
    """Mono 16-bit PCM WAV at 16 or 24 kHz -> text. Multipart: an "audio" file + a JSON "request" part."""
    import requests   # only voice needs it
    key = setting("META_API_KEY")
    if not key:
        raise NoKey("META_API_KEY is not set in tutor-lab/.env")
    for attempt in range(2):   # one retry: the corporate proxy drops TLS now and then
        started = time.time()
        try:
            resp = requests.post(ASR_URL, headers={"Authorization": f"Bearer {key}"}, timeout=120,
                                 files={"audio": ("question.wav", wav),
                                        "request": (None, json.dumps({"model": TRANSCRIBE_MODEL, "audioEncoding": "WAV"}),
                                                    "application/json")})
        except requests.RequestException as exc:
            _log(TRANSCRIBE_MODEL, purpose, None, time.time() - started, ok=False, note=type(exc).__name__)
            if attempt == 0:
                continue
            raise LLMError(f"transcription failed: {type(exc).__name__}") from None
        if resp.status_code >= 500 and attempt == 0:
            _log(TRANSCRIBE_MODEL, purpose, None, time.time() - started, ok=False, note=str(resp.status_code))
            continue
        if resp.status_code != 200:
            _log(TRANSCRIBE_MODEL, purpose, None, time.time() - started, ok=False, note=str(resp.status_code))
            raise LLMError(f"transcription failed: HTTP {resp.status_code}")
        try:
            body = resp.json()
        except ValueError:
            body = None
        if not isinstance(body, dict):   # e.g. a proxy page returned with 200
            _log(TRANSCRIBE_MODEL, purpose, None, time.time() - started, ok=False, note="bad body")
            raise LLMError("transcription failed: unexpected response")
        _log(TRANSCRIBE_MODEL, purpose, None, time.time() - started)
        return str(body.get("transcript") or "").strip()
    raise LLMError("transcription failed")
