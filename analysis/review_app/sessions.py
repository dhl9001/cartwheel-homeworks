"""Turn a Module 1 Langfuse export into session pages for human review.

Cartwheel records one trace per user turn. Traces that share
``cartwheel.session_id`` are one conversation and must be read together.
Observation arrays are not in conversation order, so tool rows are sorted
by start time.
"""

from __future__ import annotations

import json
from typing import Any


def _as_data(value: Any) -> Any:
    """Parse a JSON string, leaving every other value unchanged."""
    if isinstance(value, str):
        stripped = value.strip()
        if stripped[:1] in "[{":
            try:
                return json.loads(stripped)
            except json.JSONDecodeError:
                return value
    return value


def plain_text(value: Any) -> str:
    """Return the readable text inside a Langfuse message or part list."""
    value = _as_data(value)
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        chunks = [plain_text(item) for item in value]
        return "\n\n".join(chunk for chunk in chunks if chunk)
    if isinstance(value, dict):
        parts = value.get("parts")
        if isinstance(parts, list):
            chunks: list[str] = []
            for part in parts:
                if not isinstance(part, dict):
                    continue
                if part.get("type") not in (None, "text"):
                    continue
                content = part.get("content")
                if isinstance(content, str) and content:
                    chunks.append(content)
            if chunks:
                return "\n\n".join(chunks)
        for key in ("content", "text"):
            content = value.get(key)
            if isinstance(content, str):
                return content
    return ""


def _clip(text: str, limit: int = 140) -> str:
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def _money(value: Any) -> str:
    try:
        return f"${float(value):.2f}"
    except (TypeError, ValueError):
        return str(value)


def _query(arguments: Any) -> str:
    if isinstance(arguments, dict) and arguments.get("query"):
        return f"“{arguments['query']}”"
    return ""


def summarize_tool(name: str, arguments: Any, result: Any) -> str:
    """One visible line for a tool row. Raw JSON stays behind a click."""
    arguments = _as_data(arguments)
    result = _as_data(result)
    if not isinstance(arguments, dict):
        arguments = {}
    if isinstance(result, dict) and result.get("ok") is False:
        err = str(result.get("error") or "error")
        reason = str(result.get("reason") or "")
        return _clip(f"{err} · {reason}".strip(" ·"))
    if not isinstance(result, dict):
        return name

    if name == "cancel_order":
        return f"order {result.get('order_id')} → {result.get('status')}"
    if name == "issue_refund":
        return (
            f"order {result.get('order_id')} · {_money(result.get('amount_usd'))}"
            f" · {result.get('status')}"
        )
    if name == "get_order":
        order = result.get("order") if isinstance(result.get("order"), dict) else {}
        return (
            f"order {order.get('order_id')} · {order.get('status')}"
            f" · {order.get('store_name')}"
        )
    if name == "get_policy":
        return f"{result.get('policy_id')} · {result.get('title')}"
    if name == "search_help_center":
        results = result.get("results") if isinstance(result.get("results"), list) else []
        top = results[0].get("policy_id") if results and isinstance(results[0], dict) else "no matches"
        query = _query(arguments)
        return _clip(f"{query} · {top}".strip(" ·"))
    if name == "find_order":
        orders = result.get("orders") if isinstance(result.get("orders"), list) else []
        first = ""
        if orders and isinstance(orders[0], dict):
            first = str(orders[0].get("product_name") or "")
        query = _query(arguments)
        matched = f"{len(orders)} matches"
        return _clip(" · ".join(part for part in (query, matched, first) if part))
    if name == "list_my_orders":
        return f"{result.get('count')} orders"
    if name == "search_products":
        query = _query(arguments)
        count = f"{result.get('count')} products"
        return _clip(f"{query} · {count}".strip(" ·"))
    if name == "escalate_to_human":
        return f"ticket {result.get('ticket_id')} · {result.get('sla_hours')}h"
    if result.get("ok") is True:
        return "ok"
    return name


def _start_key(observation: dict[str, Any]) -> str:
    return str(observation.get("startTime") or observation.get("start_time") or "")


def _attrs(trace: dict[str, Any]) -> dict[str, Any]:
    metadata = trace.get("metadata")
    if not isinstance(metadata, dict):
        return {}
    attributes = metadata.get("attributes")
    return attributes if isinstance(attributes, dict) else {}


def _model_steps(observations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Plain-text reasoning and the tool each model call decided on.

    Generation inputs repeat the whole conversation, so they stay out of the
    payload. The closed row in the interface is enough until the reviewer
    asks for this list.
    """
    generations = [obs for obs in observations if obs.get("type") == "GENERATION"]
    steps: list[dict[str, Any]] = []
    for observation in sorted(generations, key=_start_key):
        texts: list[str] = []
        tools: list[str] = []
        output = _as_data(observation.get("output"))
        messages = output if isinstance(output, list) else [output]
        for message in messages:
            if not isinstance(message, dict):
                continue
            for part in message.get("parts") or []:
                if not isinstance(part, dict):
                    continue
                if part.get("type") == "text" and isinstance(part.get("content"), str):
                    texts.append(part["content"])
                if part.get("type") == "tool_call" and part.get("name"):
                    tools.append(str(part["name"]))
        steps.append({"text": "\n\n".join(texts), "tools": tools})
    return steps


def _system_prompt(observations: list[dict[str, Any]]) -> str:
    generations = [obs for obs in observations if obs.get("type") == "GENERATION"]
    for observation in sorted(generations, key=_start_key):
        payload = _as_data(observation.get("input"))
        if not isinstance(payload, dict):
            continue
        messages = payload.get("messages")
        if not isinstance(messages, list) or not messages:
            continue
        first = messages[0]
        if not isinstance(first, dict):
            continue
        text = plain_text(first)
        role = first.get("role")
        if role in {"system", "developer"} or text.startswith("You are Cartwheel"):
            return text
    return ""


def _tools(observations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    tool_obs = [obs for obs in observations if obs.get("type") == "TOOL"]
    for observation in sorted(tool_obs, key=_start_key):
        name = str(observation.get("name") or "tool")
        arguments = _as_data(observation.get("input"))
        result = _as_data(observation.get("output"))
        rows.append(
            {
                "name": name,
                "summary": summarize_tool(name, arguments, result),
                "arguments": arguments,
                "result": result,
            }
        )
    return rows


def _turn(trace: dict[str, Any]) -> dict[str, Any]:
    observations = [
        obs for obs in (trace.get("observations") or []) if isinstance(obs, dict)
    ]
    return {
        "trace_id": str(trace.get("id") or ""),
        "created_at": str(trace.get("createdAt") or trace.get("timestamp") or ""),
        "user_text": plain_text(trace.get("input")),
        "assistant_text": plain_text(trace.get("output")),
        "tools": _tools(observations),
        "model_steps": _model_steps(observations),
    }


def build_sessions(export: dict[str, Any]) -> list[dict[str, Any]]:
    """Group traces into sessions sorted by scenario id, turns by time."""
    grouped: dict[str, dict[str, Any]] = {}
    for trace in export.get("traces") or []:
        if not isinstance(trace, dict):
            continue
        attrs = _attrs(trace)
        session_id = str(attrs.get("cartwheel.session_id") or trace.get("id") or "")
        observations = [
            obs for obs in (trace.get("observations") or []) if isinstance(obs, dict)
        ]
        page = grouped.get(session_id)
        if page is None:
            page = {
                "session_id": session_id,
                "scenario_id": str(
                    trace.get("cartwheel_scenario_id")
                    or attrs.get("cartwheel.scenario_id")
                    or ""
                ),
                "role": str(attrs.get("cartwheel.user_role") or ""),
                "user_id": str(attrs.get("cartwheel.user_id") or ""),
                "system_prompt": "",
                "turns": [],
            }
            grouped[session_id] = page
        if not page["system_prompt"]:
            page["system_prompt"] = _system_prompt(observations)
        page["turns"].append(_turn(trace))

    sessions = list(grouped.values())
    for page in sessions:
        page["turns"].sort(key=lambda turn: turn["created_at"])
    sessions.sort(key=lambda page: page["scenario_id"])
    return sessions
