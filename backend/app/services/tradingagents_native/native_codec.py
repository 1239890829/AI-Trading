"""Candidate native Messages codec. No network, model, executor, or credentials.

Only provider-typed tool_use blocks can request tools. JSON/DSML in text is
always text. Tool results originate from the local executor's typed messages.
Callers must supply the ORIGINAL Pydantic tool_call_schema validator, using
model_validate(arguments, strict=True), and the original structured model
validator. No partial JSON Schema implementation is used here.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
import hashlib
import json
import math
import re
from typing import Any, Callable, Mapping, Sequence


MAX_WIRE_BYTES = 8 * 1024 * 1024
MAX_CALLS_PER_TURN = 16
NAME = re.compile(r"[A-Za-z0-9_-]{1,64}\Z")


class CodecError(ValueError):
    pass


def canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _unique(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise CodecError("duplicate JSON key")
        value[key] = item
    return value


def decode_wire_json(raw: bytes) -> dict:
    """Optional HTTP decoder before any SDK can discard duplicate JSON keys."""
    if not isinstance(raw, bytes) or len(raw) > MAX_WIRE_BYTES:
        raise CodecError("wire response is not bounded bytes")
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique,
                           parse_constant=lambda _: (_ for _ in ()).throw(CodecError("nonfinite JSON")))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise CodecError("invalid wire JSON") from exc
    if not isinstance(value, dict):
        raise CodecError("wire response must be an object")
    return value


def _json_value(value: Any, depth: int = 0) -> None:
    if depth > 64:
        raise CodecError("JSON nesting exceeds bound")
    if value is None or isinstance(value, (str, bool, int)):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise CodecError("nonfinite argument")
        return
    if isinstance(value, list):
        for item in value:
            _json_value(item, depth + 1)
        return
    if isinstance(value, dict) and all(isinstance(key, str) for key in value):
        for item in value.values():
            _json_value(item, depth + 1)
        return
    raise CodecError("argument is not JSON data")


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    input_schema: dict
    validate_original: Callable[[dict], Any] = field(repr=False, compare=False)

    def validate_arguments(self, arguments: Any) -> dict:
        if not isinstance(arguments, dict):
            raise CodecError("native tool input is not an object")
        _json_value(arguments)
        # Same root allowlist as the existing frozen worker. In particular,
        # provider output cannot inject a symbol/date/identity omitted from the
        # original tool_call_schema by InjectedToolArg.
        properties = self.input_schema.get("properties")
        if not isinstance(properties, dict):
            raise CodecError("original tool_call_schema must expose root properties")
        if set(arguments) - set(properties):
            raise CodecError("unoffered or state-injected argument")
        try:
            self.validate_original(deepcopy(arguments))
        except Exception as exc:
            raise CodecError("original tool argument validator rejected input") from exc
        return deepcopy(arguments)

    def native(self) -> dict:
        if not NAME.fullmatch(self.name) or not callable(self.validate_original):
            raise CodecError("invalid native tool contract")
        return {"name": self.name, "description": self.description,
                "input_schema": deepcopy(self.input_schema)}


@dataclass(frozen=True)
class StructuredSpec:
    name: str
    input_schema: dict
    validate_original: Callable[[dict], Any] = field(repr=False, compare=False)
    method: str = "tool"

    def native(self) -> dict:
        if (not NAME.fullmatch(self.name) or not callable(self.validate_original)
                or self.method not in {"tool", "json_schema"}):
            raise CodecError("invalid structured-output contract")
        return {"name": self.name, "description": self.input_schema.get("description", ""),
                "input_schema": deepcopy(self.input_schema)}


@dataclass(frozen=True)
class DecodedTurn:
    text: str
    tool_calls: tuple[dict, ...]
    structured: Any
    provider_content: tuple[dict, ...] = field(repr=False)
    metadata: dict

    def audit(self) -> dict:
        """No request context, tool result text, thinking text, or credentials."""
        return {"protocol": "anthropic_native_typed_blocks", "tool_calls": len(self.tool_calls),
                "tool_ids": [call["id"] for call in self.tool_calls],
                "tool_names": [call["name"] for call in self.tool_calls],
                "text_sha256": hashlib.sha256(self.text.encode()).hexdigest(),
                "structured_present": self.structured is not None,
                "provider_block_types": [block["type"] for block in self.provider_content],
                **deepcopy(self.metadata)}


def tool_map(tools: Sequence[ToolSpec]) -> dict[str, ToolSpec]:
    result = {}
    for spec in tools:
        spec.native()
        if spec.name in result:
            raise CodecError("duplicate offered tool name")
        result[spec.name] = spec
    return result


def serialize_langchain_messages(messages: Sequence[Any]) -> list[dict]:
    """Serialize public LAB LangChain messages without importing app/upstream."""
    roles = {"system": "system", "human": "user", "ai": "assistant", "tool": "tool"}
    result = []
    for message in messages:
        kind = getattr(message, "type", None)
        if kind not in roles or not isinstance(getattr(message, "content", None), str):
            raise CodecError("LAB requires supported message roles with text content")
        row = {"role": roles[kind], "content": message.content}
        if kind == "ai":
            calls = getattr(message, "tool_calls", None)
            if calls:
                row["tool_calls"] = deepcopy(calls)
            retained = getattr(message, "additional_kwargs", {}).get("lab_native_provider_content")
            if retained is not None:
                row["provider_content"] = deepcopy(retained)
        elif kind == "tool":
            row.update(tool_call_id=message.tool_call_id, name=message.name,
                       status=getattr(message, "status", "success"))
        result.append(row)
    return result


def decode_assistant(message: Mapping, tools: Sequence[ToolSpec] = (),
                     structured: StructuredSpec | None = None) -> DecodedTurn:
    """Consume native provider output without interpreting executable prose."""
    if not isinstance(message, Mapping) or message.get("role") != "assistant":
        raise CodecError("expected native assistant message")
    if len(canonical(dict(message)).encode()) > MAX_WIRE_BYTES:
        raise CodecError("response exceeds byte bound")
    allowed = tool_map(tools)
    if structured is not None and allowed:
        raise CodecError("upstream structured mode and research-tool mode are distinct")
    stop = message.get("stop_reason")
    if stop not in {"end_turn", "tool_use"}:
        # Includes refusal, max_tokens and pause_turn. No automatic textual
        # salvage can silently turn incomplete/refused output into a tool call.
        raise CodecError("nonterminal, truncated, refused, or unsupported stop reason")
    blocks = message.get("content")
    if not isinstance(blocks, list):
        raise CodecError("native content must be a typed block list")
    calls, text, retained, ids = [], [], [], set()
    structured_value = None
    for block in blocks:
        if not isinstance(block, dict) or not isinstance(block.get("type"), str):
            raise CodecError("untyped provider content block")
        kind = block["type"]
        if kind == "text":
            if not isinstance(block.get("text"), str):
                raise CodecError("invalid text block")
            text.append(block["text"])
        elif kind == "tool_use":
            call_id, name, arguments = block.get("id"), block.get("name"), block.get("input")
            if not isinstance(call_id, str) or not call_id or call_id in ids:
                raise CodecError("invalid or duplicate native tool id")
            ids.add(call_id)
            if not isinstance(name, str):
                raise CodecError("native tool name is not a string")
            if structured is not None:
                if structured.method != "tool":
                    raise CodecError("tool output is not the frozen JSON-schema mode")
                if name != structured.name or structured_value is not None or not isinstance(arguments, dict):
                    raise CodecError("expected exactly the forced structured-output tool")
                _json_value(arguments)
                try:
                    structured_value = structured.validate_original(deepcopy(arguments))
                except Exception as exc:
                    raise CodecError("original structured model rejected input") from exc
            else:
                if name not in allowed:
                    raise CodecError("provider requested an unoffered tool")
                args = allowed[name].validate_arguments(arguments)
                calls.append({"id": call_id, "name": name, "args": args, "type": "tool_call"})
        elif kind in {"thinking", "redacted_thinking"}:
            # Opaque native continuation material, retained only for the caller's
            # in-memory provider round trip. audit() never returns this payload.
            pass
        else:
            # Refuses assistant-origin tool_result, server_tool_use, computer,
            # browsing and any newly introduced protocol block by default.
            raise CodecError("unsupported or assistant-origin result block")
        retained.append(deepcopy(block))
    if len(calls) > MAX_CALLS_PER_TURN:
        raise CodecError("too many calls in one provider turn")
    has_use = any(block["type"] == "tool_use" for block in blocks)
    if has_use != (stop == "tool_use"):
        raise CodecError("native stop reason disagrees with typed calls")
    if structured is not None and structured.method == "json_schema":
        if stop != "end_turn" or has_use:
            raise CodecError("JSON-schema mode requires one final JSON document")
        value = decode_wire_json("".join(text).encode())
        try:
            structured_value = structured.validate_original(value)
        except Exception as exc:
            raise CodecError("original structured model rejected JSON document") from exc
    if structured is not None and structured_value is None:
        raise CodecError("provider did not return required structured result")
    usage = deepcopy(message.get("usage")) if isinstance(message.get("usage"), dict) else None
    return DecodedTurn("".join(text), tuple(calls), structured_value, tuple(retained),
                       {"stop_reason": stop, "provider_model_reported": message.get("model"),
                        "provider_message_id": message.get("id"), "usage": usage,
                        "actual_cost_usd": None, "hidden_transport_retries": "unknown"})


def encode_history(messages: Sequence[Mapping], tools: Sequence[ToolSpec]) -> tuple[list[dict], list[dict]]:
    """Preserve roles/ids. Group parallel ToolMessages into one result message.

    Input is typed worker data, not model-authored text. The worker must serialize
    AIMessage.tool_calls and ToolMessage.tool_call_id as fields, never in content.
    """
    allowed = tool_map(tools)
    system, wire, pending, seen = [], [], {}, set()
    for message in messages:
        if not isinstance(message, Mapping) or not isinstance(message.get("role"), str):
            raise CodecError("untyped history message")
        role, content = message["role"], message.get("content", "")
        if role == "system":
            if wire or pending or not isinstance(content, str):
                raise CodecError("system messages must precede conversation and be text")
            system.append({"type": "text", "text": content})
            continue
        if role == "tool":
            call_id = message.get("tool_call_id")
            if call_id not in pending or not isinstance(content, str):
                raise CodecError("orphan, duplicate, or nontext tool result")
            if message.get("name") is not None and message["name"] != pending[call_id]:
                raise CodecError("tool result name mismatches issued call")
            block = {"type": "tool_result", "tool_use_id": call_id, "content": content}
            status = message.get("status", "success")
            if status not in {"success", "error"}:
                raise CodecError("unknown executor tool status")
            if status == "error":
                block["is_error"] = True
            if not wire or wire[-1]["role"] != "user" or not wire[-1].get("_results"):
                wire.append({"role": "user", "content": [], "_results": True})
            wire[-1]["content"].append(block)
            del pending[call_id]
            continue
        if pending:
            raise CodecError("all tool results must immediately follow issued calls")
        if role == "user":
            if not isinstance(content, str):
                raise CodecError("user evidence must remain inert text")
            wire.append({"role": "user", "content": [{"type": "text", "text": content}]})
        elif role == "assistant":
            if not isinstance(content, str):
                raise CodecError("assistant display content must be text")
            calls = message.get("tool_calls", [])
            if not isinstance(calls, list) or len(calls) > MAX_CALLS_PER_TURN:
                raise CodecError("invalid typed assistant tool_calls")
            blocks = [{"type": "text", "text": content}] if content else []
            for call in calls:
                if not isinstance(call, dict) or set(call) - {"id", "name", "args", "type"}:
                    raise CodecError("invalid worker tool call shape")
                call_id, name = call.get("id"), call.get("name")
                if not isinstance(call_id, str) or not call_id or call_id in seen or name not in allowed:
                    raise CodecError("invalid repeated id or unoffered history tool")
                arguments = allowed[name].validate_arguments(call.get("args"))
                blocks.append({"type": "tool_use", "id": call_id, "name": name, "input": arguments})
                pending[call_id] = name
                seen.add(call_id)
            if message.get("provider_content") is not None:
                native_blocks = message["provider_content"]
                decoded = decode_assistant({"role": "assistant", "content": native_blocks,
                                            "stop_reason": "tool_use" if calls else "end_turn"}, tools)
                expected_calls = [{"id": item["id"], "name": item["name"], "args": item["args"], "type": "tool_call"}
                                  for item in calls]
                if decoded.text != content or list(decoded.tool_calls) != expected_calls:
                    raise CodecError("native history blocks disagree with typed worker message")
                blocks = deepcopy(native_blocks)
            if not blocks:
                raise CodecError("empty assistant message")
            wire.append({"role": "assistant", "content": blocks})
        else:
            raise CodecError("unknown history role")
    if pending:
        raise CodecError("request ends with unresolved tool calls")
    for item in wire:
        item.pop("_results", None)
    return system, wire


def build_request(messages: Sequence[Mapping], model: str, max_tokens: int,
                  tools: Sequence[ToolSpec] = (), structured: StructuredSpec | None = None,
                  supports_forced_tool_choice: bool = False) -> dict:
    if not isinstance(model, str) or not model or not isinstance(max_tokens, int) or isinstance(max_tokens, bool) or max_tokens <= 0:
        raise CodecError("model identity and explicit token limit required")
    if tools and structured is not None:
        raise CodecError("research tools and structured output are separate original node modes")
    system, wire = encode_history(messages, tools)
    request = {"model": model, "max_tokens": max_tokens, "messages": wire}
    if system:
        request["system"] = system
    if structured is not None:
        definition = structured.native()
        if structured.method == "json_schema":
            request["output_config"] = {"format": {"type": "json_schema", "schema": definition["input_schema"]}}
        else:
            request["tools"] = [definition]
            if supports_forced_tool_choice:
                request["tool_choice"] = {"type": "tool", "name": structured.name}
    elif tools:
        request["tools"] = [tool.native() for tool in tools]
    return request


def diagnose_legacy_text(text: str) -> dict:
    """Offline diagnosis only. This function can NEVER return executable calls."""
    if not isinstance(text, str) or len(text.encode()) > MAX_WIRE_BYTES:
        raise CodecError("legacy input exceeds bound")
    stripped = text.lstrip()
    decoded, remainder = None, stripped
    if stripped.startswith("{"):
        try:
            decoded, used = json.JSONDecoder(object_pairs_hook=_unique).raw_decode(stripped)
            remainder = stripped[used:].strip()
        except (ValueError, TypeError):
            pass
    return {"protocol": "legacy_text_not_executable", "executable_tool_calls": 0,
            "leading_json_value": isinstance(decoded, dict),
            "has_trailing_text_after_json": isinstance(decoded, dict) and bool(remainder),
            "has_assistant_authored_tool_result_text": bool(re.search(r'"tool_call_id"\s*:', remainder)),
            "has_dsml_text": "｜｜DSML｜｜" in text,
            "sha256": hashlib.sha256(text.encode()).hexdigest(),
            "reason": "Typed provider metadata is required; prose cannot prove a tool call or executor result."}
