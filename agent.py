"""The shared Muse Spark Open tool loop: call -> run tools -> append -> repeat, until a JSON answer."""
from __future__ import annotations

import json

import llm
import thoughts
import tools


def _assistant_message(msg) -> dict:
    out = {"role": "assistant", "content": msg.content or ""}
    if msg.tool_calls:
        out["tool_calls"] = [{"id": tc.id, "type": "function",
                              "function": {"name": tc.function.name, "arguments": tc.function.arguments}}
                             for tc in msg.tool_calls]
    return out


def run(messages: list[dict], *, purpose: str, max_steps: int, max_tokens: int = 6000,
        temperature: float | None = None) -> tuple[dict | None, list[dict]]:
    """Run the loop on `messages` (appended in place). Returns (parsed final JSON or None, tool trace)."""
    trace: list[dict] = []
    for _ in range(max_steps):
        msg = llm.chat(messages, purpose=purpose, tools=tools.AGENT_TOOLS, effort="low", max_tokens=max_tokens,
                       temperature=temperature)
        messages.append(_assistant_message(msg))
        if not msg.tool_calls:
            try:
                return llm.parse_json(msg.content), trace
            except ValueError:
                return None, trace
        for tc in msg.tool_calls:
            try:
                args = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {}
            args = args if isinstance(args, dict) else {}
            if tc.function.name == "read_save":
                save = tools.saves().get(str(args.get("save_id", "")), {})
                if save.get("title"):
                    thoughts.step(f"Reading “{save['title'][:80]}”…")
                    thoughts.focus(f"{save['title']} {save.get('summary', '')} {' '.join(save.get('concepts', []))}")
            elif tc.function.name == "search_saves" and args.get("query"):
                thoughts.step(f"Searching your saves for “{str(args['query'])[:60]}”…")
            result = tools.run_tool(tc.function.name, args)
            trace.append({"tool": tc.function.name, "args": args})
            messages.append({"role": "tool", "tool_call_id": tc.id,
                             "content": json.dumps(result, ensure_ascii=False)[:8000]})
    return None, trace
