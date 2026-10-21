"""Official CLI --json-schema codec candidate; no model/network/executor.

Only CLI's typed structured_output field is consumed. .result is never parsed.
The graph still receives AIMessage.tool_calls and executes its original tools.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import hashlib
from typing import Any, Mapping, Sequence

from .native_codec import (CodecError, MAX_CALLS_PER_TURN, MAX_WIRE_BYTES,
                          StructuredSpec, ToolSpec, _json_value, canonical, tool_map)


def _rebase_refs(schema: Any, pointer: str) -> Any:
    if isinstance(schema, list):
        return [_rebase_refs(value, pointer) for value in schema]
    if not isinstance(schema, dict):
        return deepcopy(schema)
    if "$id" in schema or "$anchor" in schema or "$dynamicRef" in schema:
        raise CodecError("unreviewed schema reference scope; do not silently weaken it")
    result = {}
    for key, value in schema.items():
        if key == "$ref":
            if not isinstance(value, str) or not (value == "#" or value.startswith("#/")):
                raise CodecError("only local original schema references are supported")
            result[key] = pointer + value[1:]
        else:
            result[key] = _rebase_refs(value, pointer)
    return result


def build_cli_schema(tools: Sequence[dict] | None = None, schema: dict | None = None) -> dict:
    """tools are the untouched convert_to_openai_tool results from the worker."""
    if schema is not None:
        if tools or not isinstance(schema, dict):
            raise CodecError("original structured node mode cannot also offer research tools")
        return deepcopy(schema)  # Original Pydantic schema, including enums/normalizers.
    choices, names = [], set()
    for index, tool in enumerate(tools or []):
        if not isinstance(tool, dict) or tool.get("type") != "function":
            raise CodecError("expected original OpenAI-format function specification")
        function = tool.get("function", {})
        name, arguments = function.get("name"), function.get("parameters")
        if not isinstance(name, str) or not name or name in names or not isinstance(arguments, dict):
            raise CodecError("invalid or duplicate original tool specification")
        names.add(name)
        pointer = f"#/properties/tool_calls/items/oneOf/{index}/properties/arguments"
        description = function.get("description", "")
        if not isinstance(description, str):
            raise CodecError("original tool description must be text")
        choices.append({"type": "object", "description": description, "properties": {
            "id": {"type": "string", "minLength": 1},
            "name": {"type": "string", "const": name},
            "arguments": _rebase_refs(arguments, pointer)},
            "required": ["id", "name", "arguments"], "additionalProperties": False})
    calls = {"type": "array", "items": {"oneOf": choices} if choices else {"type": "object"},
             "maxItems": MAX_CALLS_PER_TURN if choices else 0}
    return {"type": "object", "properties": {"content": {"type": "string"}, "tool_calls": calls},
            "required": ["content", "tool_calls"], "additionalProperties": False}


def encode_cli_transcript(messages: Sequence[Mapping]) -> str:
    """Keep the exact ordered role/ID/argument fields as JSON; never join bodies."""
    copied, pending = [], {}
    for message in messages:
        if not isinstance(message, Mapping) or message.get("role") not in {"system", "user", "assistant", "tool"}:
            raise CodecError("invalid transcript role")
        if not isinstance(message.get("content"), str):
            raise CodecError("public LAB transcript content must be text")
        row = deepcopy(dict(message))
        role = row["role"]
        if role == "tool":
            call_id = row.get("tool_call_id")
            if call_id not in pending:
                raise CodecError("orphan or duplicate transcript tool result")
            if row.get("name") is not None and row["name"] != pending[call_id]:
                raise CodecError("transcript result name disagrees with call")
            if row.get("status", "success") not in {"success", "error"}:
                raise CodecError("unknown tool result status")
            del pending[call_id]
        else:
            if pending:
                raise CodecError("all actual tool results must precede the next conversation message")
            if role == "assistant":
                calls = row.get("tool_calls", [])
                if not isinstance(calls, list) or len(calls) > MAX_CALLS_PER_TURN:
                    raise CodecError("invalid transcript tool call list")
                for call in calls:
                    if not isinstance(call, dict) or set(call) - {"id", "name", "args", "type"}:
                        raise CodecError("invalid typed transcript call")
                    call_id, name = call.get("id"), call.get("name")
                    if not isinstance(call_id, str) or not call_id or call_id in pending or not isinstance(name, str):
                        raise CodecError("invalid transcript call identity")
                    if not isinstance(call.get("args"), dict):
                        raise CodecError("transcript call arguments must be an object")
                    pending[call_id] = name
        _json_value(row)
        copied.append(row)
    if pending:
        raise CodecError("transcript has unresolved tool calls")
    text = canonical({"format": "ordered-role-tool-transcript-v1", "messages": copied})
    if len(text.encode()) > MAX_WIRE_BYTES:
        raise CodecError("transcript exceeds byte bound")
    return text


@dataclass(frozen=True)
class CliDecodedTurn:
    text: str
    tool_calls: tuple[dict, ...]
    structured: Any
    id_mapping: tuple[dict, ...] = ()


def decode_cli_output(value: dict, tools: Sequence[ToolSpec] = (),
                      structured: StructuredSpec | None = None,
                      call_namespace: str | None = None) -> CliDecodedTurn:
    """value must be the official CLI typed field, never a parsed result string."""
    if not isinstance(value, dict):
        raise CodecError("CLI structured_output must be an object")
    _json_value(value)
    if len(canonical(value).encode()) > MAX_WIRE_BYTES:
        raise CodecError("CLI structured output exceeds byte bound")
    if structured is not None:
        if tools:
            raise CodecError("structured node mode cannot also offer research tools")
        try:
            parsed = structured.validate_original(deepcopy(value))
        except Exception as exc:
            raise CodecError("original Pydantic result model rejected CLI typed output") from exc
        return CliDecodedTurn("", (), parsed)
    if set(value) != {"content", "tool_calls"} or not isinstance(value["content"], str) or not isinstance(value["tool_calls"], list):
        raise CodecError("CLI envelope requires exactly text content and typed tool_calls")
    if len(value["tool_calls"]) > MAX_CALLS_PER_TURN:
        raise CodecError("too many declared calls")
    allowed, ids, calls, mapping = tool_map(tools), set(), [], []
    for index, call in enumerate(value["tool_calls"]):
        if not isinstance(call, dict) or set(call) != {"id", "name", "arguments"}:
            raise CodecError("model tool_result/extra fields cannot be graph calls")
        call_id, name = call["id"], call["name"]
        if not isinstance(call_id, str) or not call_id or call_id in ids:
            raise CodecError("invalid or duplicate declared call id")
        if not isinstance(name, str) or name not in allowed:
            raise CodecError("CLI declared an unoffered function")
        args = allowed[name].validate_arguments(call["arguments"])
        ids.add(call_id)
        graph_id = f"cli-{call_namespace}-{index}" if call_namespace is not None else call_id
        mapping.append({"model_id": call_id, "graph_id": graph_id})
        calls.append({"id": graph_id, "name": name, "args": args, "type": "tool_call"})
    return CliDecodedTurn(value["content"], tuple(calls), None, tuple(mapping))


def safe_terminal_metadata(response: Mapping) -> dict:
    """Record only terminal facts needed to distinguish success/error/unknown."""
    terminal = response.get("cli_terminal")
    if not isinstance(terminal, dict) or terminal.get("is_error") is not False or terminal.get("subtype") != "success":
        raise CodecError("CLI did not return a successful typed terminal event")
    return {key: deepcopy(terminal.get(key)) for key in ("subtype", "is_error", "num_turns")}


def output_digest(value: dict) -> str:
    return hashlib.sha256(canonical(value).encode()).hexdigest()


# Explicit names for the broker/worker contract; no text-parser fallback.
make_cli_schema = build_cli_schema
decode_cli_envelope = decode_cli_output
