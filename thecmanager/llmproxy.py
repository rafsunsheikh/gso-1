"""Anthropic-compatible shim so Claude Code can talk to the local llama-server.

Exposes the Anthropic Messages API (`/v1/messages`) and translates to the
llama.cpp server's OpenAI Chat Completions API. This lets `local` Claude
sessions run entirely on your machine: Claude Code -> GSO-1 /v1/messages ->
llama-server -> back.

Tool calls and streaming are translated both ways. Tool-use reliability still
depends on the local model's function-calling ability (run llama-server with
`--jinja` and a tool-capable model).
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Iterator

from . import llm

_STOP_MAP = {"stop": "end_turn", "length": "max_tokens", "tool_calls": "tool_use"}


def _llama_base() -> str:
    st = llm.status()
    port = st.get("port") or llm.DEFAULT_PORT
    return f"http://{llm.HOST}:{port}"


# --------------------------------------------------------------------------
# Anthropic request -> OpenAI request
# --------------------------------------------------------------------------
def to_openai(body: dict) -> dict:
    msgs: list[dict] = []
    system = body.get("system")
    if isinstance(system, list):
        system = "\n".join(b.get("text", "") for b in system if b.get("type") == "text")
    if system:
        msgs.append({"role": "system", "content": system})

    for m in body.get("messages", []):
        role = m.get("role", "user")
        content = m.get("content")
        if isinstance(content, str):
            msgs.append({"role": role, "content": content})
            continue
        text_parts, tool_calls, tool_results = [], [], []
        for blk in content or []:
            bt = blk.get("type")
            if bt == "text":
                text_parts.append(blk.get("text", ""))
            elif bt == "tool_use":
                tool_calls.append({
                    "id": blk.get("id"),
                    "type": "function",
                    "function": {"name": blk.get("name", ""),
                                 "arguments": json.dumps(blk.get("input", {}))},
                })
            elif bt == "tool_result":
                c = blk.get("content", "")
                if isinstance(c, list):
                    c = "\n".join(x.get("text", "") for x in c
                                  if isinstance(x, dict) and x.get("type") == "text") or json.dumps(c)
                tool_results.append({"role": "tool", "tool_call_id": blk.get("tool_use_id"),
                                     "content": c if isinstance(c, str) else json.dumps(c)})
        if role == "assistant":
            am: dict = {"role": "assistant", "content": "\n".join(text_parts) or None}
            if tool_calls:
                am["tool_calls"] = tool_calls
            msgs.append(am)
        else:
            if text_parts:
                msgs.append({"role": "user", "content": "\n".join(text_parts)})
            msgs.extend(tool_results)

    out: dict = {"messages": msgs}
    if body.get("max_tokens"):
        out["max_tokens"] = body["max_tokens"]
    if body.get("temperature") is not None:
        out["temperature"] = body["temperature"]
    # Forward the rest of the sampler so per-call settings reach llama-server
    # (previously only temperature was passed; top_p/top_k/min_p were dropped).
    if body.get("top_p") is not None:
        out["top_p"] = body["top_p"]
    if body.get("top_k") is not None:
        out["top_k"] = body["top_k"]
    if body.get("min_p") is not None:
        out["min_p"] = body["min_p"]
    # Reuse llama.cpp's prompt cache across turns for faster prefill.
    out["cache_prompt"] = True
    tools = body.get("tools")
    if tools:
        out["tools"] = [{"type": "function",
                         "function": {"name": t["name"], "description": t.get("description", ""),
                                      "parameters": t.get("input_schema", {})}} for t in tools]
        out["tool_choice"] = "auto"
    return out


def _post(path: str, payload: dict, stream: bool, timeout: int = 600):
    req = urllib.request.Request(
        _llama_base() + path,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    return urllib.request.urlopen(req, timeout=timeout)


def _describe_error(e: Exception) -> str:
    """Surface llama-server's actual error body (e.g. 'exceeds the available
    context size') instead of just 'HTTP Error 500', which is far easier to
    diagnose than the bare urllib exception string."""
    body = ""
    try:
        if isinstance(e, urllib.error.HTTPError):
            raw = e.read().decode("utf-8", "ignore")
            try:
                j = json.loads(raw)
                body = (j.get("error", {}) or {}).get("message") or j.get("message") or raw
            except Exception:
                body = raw
    except Exception:
        pass
    return f"{e}{(': ' + body.strip()) if body else ''}"


# --------------------------------------------------------------------------
# Non-streaming: OpenAI response -> Anthropic message
# --------------------------------------------------------------------------
def complete(body: dict) -> dict:
    payload = to_openai(body)
    payload["stream"] = False
    try:
        with _post("/v1/chat/completions", payload, stream=False) as r:
            oai = json.loads(r.read().decode())
    except Exception as e:  # noqa: BLE001
        return {
            "id": "msg_error", "type": "message", "role": "assistant",
            "model": body.get("model", "local"),
            "content": [{"type": "text", "text": f"[local LLM proxy error: {_describe_error(e)}]"}],
            "stop_reason": "end_turn", "stop_sequence": None,
            "usage": {"input_tokens": 0, "output_tokens": 0},
        }
    choice = (oai.get("choices") or [{}])[0]
    msg = choice.get("message", {}) or {}
    content = []
    if msg.get("content"):
        content.append({"type": "text", "text": msg["content"]})
    for tc in msg.get("tool_calls", []) or []:
        try:
            inp = json.loads(tc.get("function", {}).get("arguments") or "{}")
        except Exception:
            inp = {}
        content.append({"type": "tool_use", "id": tc.get("id") or "toolu_local",
                        "name": tc.get("function", {}).get("name", ""), "input": inp})
    if not content:
        content = [{"type": "text", "text": ""}]
    usage = oai.get("usage", {}) or {}
    return {
        "id": "msg_" + (oai.get("id", "local").replace("chatcmpl-", "")),
        "type": "message", "role": "assistant", "model": body.get("model", "local"),
        "content": content,
        "stop_reason": _STOP_MAP.get(choice.get("finish_reason"), "end_turn"),
        "stop_sequence": None,
        "usage": {"input_tokens": usage.get("prompt_tokens", 0),
                  "output_tokens": usage.get("completion_tokens", 0)},
    }


# --------------------------------------------------------------------------
# Streaming: OpenAI SSE -> Anthropic SSE
# --------------------------------------------------------------------------
def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


def _estimate_input(body: dict) -> int:
    return max(1, len(json.dumps(body.get("messages", []))) // 4)


def stream(body: dict) -> Iterator[str]:
    model = body.get("model", "local")
    payload = to_openai(body)
    payload["stream"] = True

    yield _sse("message_start", {"type": "message_start", "message": {
        "id": "msg_local", "type": "message", "role": "assistant", "model": model,
        "content": [], "stop_reason": None, "stop_sequence": None,
        "usage": {"input_tokens": _estimate_input(body), "output_tokens": 0}}})

    text_open, text_idx = False, 0
    tool_blocks: dict[int, int] = {}
    next_idx, out_tokens, finish = 0, 0, "end_turn"

    try:
        resp = _post("/v1/chat/completions", payload, stream=True)
        for raw in resp:
            line = raw.decode("utf-8", "ignore").strip()
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                break
            try:
                chunk = json.loads(data)
            except Exception:
                continue
            choice = (chunk.get("choices") or [{}])[0]
            delta = choice.get("delta", {}) or {}

            text = delta.get("content")
            if text:
                if not text_open:
                    text_idx = next_idx; next_idx += 1; text_open = True
                    yield _sse("content_block_start", {"type": "content_block_start",
                        "index": text_idx, "content_block": {"type": "text", "text": ""}})
                yield _sse("content_block_delta", {"type": "content_block_delta",
                    "index": text_idx, "delta": {"type": "text_delta", "text": text}})
                out_tokens += 1

            for tc in delta.get("tool_calls", []) or []:
                oi = tc.get("index", 0)
                if oi not in tool_blocks:
                    if text_open:
                        yield _sse("content_block_stop", {"type": "content_block_stop", "index": text_idx})
                        text_open = False
                    a_idx = next_idx; next_idx += 1; tool_blocks[oi] = a_idx
                    fn = tc.get("function", {}) or {}
                    yield _sse("content_block_start", {"type": "content_block_start", "index": a_idx,
                        "content_block": {"type": "tool_use", "id": tc.get("id") or f"toolu_{a_idx}",
                                          "name": fn.get("name", ""), "input": {}}})
                args = (tc.get("function", {}) or {}).get("arguments")
                if args:
                    yield _sse("content_block_delta", {"type": "content_block_delta",
                        "index": tool_blocks[oi],
                        "delta": {"type": "input_json_delta", "partial_json": args}})

            if choice.get("finish_reason"):
                finish = _STOP_MAP.get(choice["finish_reason"], "end_turn")
    except Exception as e:  # noqa: BLE001
        if not text_open and not tool_blocks:
            yield _sse("content_block_start", {"type": "content_block_start", "index": 0,
                "content_block": {"type": "text", "text": ""}})
            text_open, text_idx = True, 0
        yield _sse("content_block_delta", {"type": "content_block_delta", "index": text_idx,
            "delta": {"type": "text_delta", "text": f"[local LLM proxy error: {_describe_error(e)}]"}})

    if text_open:
        yield _sse("content_block_stop", {"type": "content_block_stop", "index": text_idx})
    for a_idx in tool_blocks.values():
        yield _sse("content_block_stop", {"type": "content_block_stop", "index": a_idx})
    yield _sse("message_delta", {"type": "message_delta",
        "delta": {"stop_reason": finish, "stop_sequence": None},
        "usage": {"output_tokens": out_tokens}})
    yield _sse("message_stop", {"type": "message_stop"})


def count_tokens(body: dict) -> dict:
    blob = json.dumps(body.get("messages", [])) + json.dumps(body.get("system", ""))
    return {"input_tokens": max(1, len(blob) // 4)}
