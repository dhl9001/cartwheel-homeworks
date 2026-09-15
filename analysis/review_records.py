"""Build review records from Langfuse traces plus scenario expected outcomes.

The Langfuse normalizer keeps every observation, including duplicate
``openai.response`` generations and an empty agent span. Reviewers need the
user/tool/assistant sequence in time order, with multi-turn scenarios stitched
from their per-turn traces, and the expected outcome from the scenario file.
"""

from __future__ import annotations

import json
import statistics
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from analysis.helpers import _state, langfuse_io

REPO_ROOT = Path(__file__).resolve().parents[1]
SCENARIO_FILES = (
    REPO_ROOT / "scenarios" / "pilot_scenarios.jsonl",
    REPO_ROOT / "scenarios" / "support_scenarios.jsonl",
)

# Observation names that are telemetry, not content the reviewer should read.
_SKIP_OBS_NAMES = {
    "openai.response",
    "Agent Workflow",
    "cartwheel-support.agent",
}

_RETRIEVAL_TOOLS = {"search_help_center", "get_policy"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_scenarios() -> dict[str, dict[str, Any]]:
    """Index scenario records by id from the committed JSONL files."""
    out: dict[str, dict[str, Any]] = {}
    for path in SCENARIO_FILES:
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            record = json.loads(line)
            scenario_id = record.get("id")
            if scenario_id:
                out[str(scenario_id)] = record
    return out


def _walk_text(value: Any, texts: list[str], *, roles: set[str] | None = None) -> None:
    if value is None:
        return
    if isinstance(value, str):
        if value.strip():
            texts.append(value)
        return
    if isinstance(value, list):
        for item in value:
            _walk_text(item, texts, roles=roles)
        return
    if not isinstance(value, dict):
        return
    role = value.get("role")
    if roles is not None and role is not None and role not in roles:
        return
    if value.get("type") == "tool_call":
        return
    content = value.get("content")
    if isinstance(content, str) and value.get("type") in (None, "text"):
        if content.strip():
            texts.append(content)
        return
    if "parts" in value:
        _walk_text(value["parts"], texts, roles=None)
        return
    if "messages" in value:
        _walk_text(value["messages"], texts, roles=roles)
        return
    if isinstance(value.get("text"), str) and value["text"].strip():
        texts.append(value["text"])


def _plain_text(value: Any, *, roles: set[str] | None = None) -> str:
    texts: list[str] = []
    _walk_text(value, texts, roles=roles)
    return "\n\n".join(texts).strip()


def _tool_name(observation: dict[str, Any]) -> str:
    return str(observation.get("name") or "tool")


def _is_tool(observation: dict[str, Any]) -> bool:
    if str(observation.get("type") or "").upper() == "TOOL":
        return True
    name = _tool_name(observation).lower()
    return "tool" in name and _tool_name(observation) not in _SKIP_OBS_NAMES


def reconstruct_messages(trace: dict[str, Any]) -> list[dict[str, Any]]:
    """Return the user / tool / assistant sequence for one Langfuse trace."""
    observations = list(trace.get("observations") or [])
    observations.sort(
        key=lambda obs: (obs.get("start_time") or "", obs.get("id") or "")
    )

    user_text = ""
    assistant_text = ""
    tools: list[dict[str, Any]] = []

    for observation in observations:
        name = _tool_name(observation)
        if name in _SKIP_OBS_NAMES:
            continue
        if name == "cartwheel.session_message":
            user_text = _plain_text(observation.get("input"), roles={"user"}) or user_text
            assistant_text = (
                _plain_text(observation.get("output"), roles={"assistant"})
                or assistant_text
            )
            continue
        if _is_tool(observation):
            tools.append(
                {
                    "name": name,
                    "arguments": observation.get("input"),
                    "content": observation.get("output"),
                }
            )

    if not user_text:
        user_text = _plain_text(trace.get("input"), roles={"user"})
    if not assistant_text:
        assistant_text = _plain_text(trace.get("output"), roles={"assistant"})

    messages: list[dict[str, Any]] = []
    if user_text:
        messages.append({"role": "user", "text": user_text})
    for tool in tools:
        retrieval = tool["name"] in _RETRIEVAL_TOOLS
        messages.append(
            {
                "role": "tool_call",
                "name": tool["name"],
                "arguments": tool["arguments"],
                "retrieval": retrieval,
            }
        )
        messages.append(
            {
                "role": "tool_result",
                "name": tool["name"],
                "content": tool["content"],
                "retrieval": retrieval,
            }
        )
    if assistant_text:
        messages.append({"role": "assistant", "text": assistant_text})
    return messages


def _flatten(messages: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    for message in messages:
        role = str(message.get("role") or "step")
        if role == "tool_call":
            content = json.dumps(message.get("arguments"), default=str)
        else:
            raw = message.get("text", message.get("content"))
            content = raw if isinstance(raw, str) else json.dumps(raw, default=str)
        if content:
            parts.append(f"{role}: {content}")
    return "\n".join(parts)


def _features(messages: list[dict[str, Any]], source_traces: list[dict[str, Any]]) -> dict[str, Any]:
    tool_calls = [m for m in messages if m.get("role") == "tool_call"]
    tools = {str(m.get("name")) for m in tool_calls if m.get("name")}
    retrieval = any(m.get("retrieval") for m in messages)
    tokens = 0
    for trace in source_traces:
        tokens += int((trace.get("features") or {}).get("tokens") or 0)
    return {
        "turn_count": sum(m.get("role") in {"user", "assistant"} for m in messages),
        "tool_call_count": len(tool_calls),
        "distinct_tools": len(tools),
        "has_retrieval": int(retrieval),
        "tokens": tokens,
        "langfuse_trace_count": len(source_traces),
    }


def _langfuse_host() -> str:
    import os

    return (os.environ.get("LANGFUSE_HOST") or "http://localhost:3100").rstrip("/")


def _permalink(trace_id: str) -> str:
    return f"{_langfuse_host()}/project/cartwheel-dev/traces/{trace_id}"


def _expected_panel(scenario: dict[str, Any] | None) -> dict[str, Any] | None:
    if not scenario:
        return None
    expected = scenario.get("expected")
    if not isinstance(expected, dict):
        return None
    tuple_data = scenario.get("tuple") if isinstance(scenario.get("tuple"), dict) else {}
    return {
        "evaluation": expected.get("evaluation"),
        "outcome": expected.get("outcome"),
        "criterion": expected.get("criterion"),
        "reason": expected.get("reason"),
        "source": expected.get("source"),
        "scenario_group": scenario.get("scenario_group"),
        "opening_message": scenario.get("opening_message"),
        "followups": scenario.get("followups") or [],
        "tuple": tuple_data,
        "data_quality_case_id": scenario.get("data_quality_case_id"),
    }


def _merge_meta(traces: list[dict[str, Any]], scenario_id: str) -> dict[str, Any]:
    meta: dict[str, Any] = {"scenario_id": scenario_id}
    for trace in traces:
        for key, value in (trace.get("meta") or {}).items():
            if value is not None and key not in meta:
                meta[key] = value
    return meta


def build_samples(
    traces: list[dict[str, Any]],
    scenarios: dict[str, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Group Langfuse traces by scenario and attach expected outcomes."""
    scenarios = scenarios if scenarios is not None else load_scenarios()
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for trace in traces:
        scenario_id = str((trace.get("meta") or {}).get("scenario_id") or trace["trace_id"])
        grouped[scenario_id].append(trace)

    samples: list[dict[str, Any]] = []
    for scenario_id, members in grouped.items():
        members.sort(key=lambda item: item.get("timestamp") or "")
        messages: list[dict[str, Any]] = []
        for index, member in enumerate(members):
            if index:
                messages.append(
                    {
                        "role": "turn_break",
                        "label": f"Follow-up turn {index + 1}",
                    }
                )
            messages.extend(reconstruct_messages(member))
        primary = members[-1]
        scenario = scenarios.get(scenario_id)
        expected = _expected_panel(scenario)
        samples.append(
            {
                "trace_id": primary["trace_id"],
                "trace_ids": [item["trace_id"] for item in members],
                "reason": "all pilot traces, grouped by scenario",
                "trace": messages,
                "text": _flatten(messages),
                "features": _features(messages, members),
                "meta": _merge_meta(members, scenario_id),
                "expected": expected,
                "permalink": _permalink(primary["trace_id"]),
                "flags": [],
            }
        )
    samples.sort(key=lambda item: str((item.get("meta") or {}).get("scenario_id") or item["trace_id"]))
    _attach_outlier_flags(samples)
    return samples


def _attach_outlier_flags(samples: list[dict[str, Any]]) -> None:
    if len(samples) < 4:
        return
    labels = {
        "tool_call_count": "tool calls",
        "turn_count": "turns",
        "tokens": "tokens",
    }
    for key, label in labels.items():
        values = [int((item.get("features") or {}).get(key) or 0) for item in samples]
        median = statistics.median(values)
        n = len(values)
        for sample, value in zip(samples, values):
            high_share = sum(1 for other in values if other >= value) / n
            low_share = sum(1 for other in values if other <= value) / n
            if value > median and high_share <= 0.10:
                sample["flags"].append(
                    f"{value} {label} (more than {int(round((1 - high_share) * 100))}%)"
                )
            elif value < median and low_share <= 0.10:
                sample["flags"].append(f"{value} {label} (bottom 10%)")


def refresh_review_samples() -> list[dict[str, Any]]:
    """Pull live Langfuse traces, write ``state/samples.json``, and return them."""
    from observability.instrument import load_env

    load_env()
    if not langfuse_io.is_configured():
        raise langfuse_io.LangfuseNotConfigured(
            "Langfuse is not configured. Set LANGFUSE_PUBLIC_KEY, "
            "LANGFUSE_SECRET_KEY, and LANGFUSE_HOST."
        )
    traces = langfuse_io.fetch_traces()
    if not traces:
        raise ValueError("Langfuse returned no traces with cartwheel.scenario_id")
    samples = build_samples(traces)
    _state.write_json(_state.state_path("samples.json"), samples)
    _state.write_json(
        _state.state_path("sample_manifest.json"),
        {
            "source": "langfuse",
            "k": len(samples),
            "strategy": "all_grouped_by_scenario",
            "selected_at": _now(),
            "picks": [
                {
                    "trace_id": sample["trace_id"],
                    "scenario_id": (sample.get("meta") or {}).get("scenario_id"),
                    "reason": sample.get("reason"),
                }
                for sample in samples
            ],
        },
    )
    return samples


def main() -> None:
    samples = refresh_review_samples()
    print(f"wrote {len(samples)} review records to {_state.state_path('samples.json')}")


if __name__ == "__main__":
    main()
