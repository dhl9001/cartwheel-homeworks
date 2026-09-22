"""Session pages group turns and keep tool outcomes on one visible line."""

from __future__ import annotations

import json
from pathlib import Path

from analysis.review_app.sessions import build_sessions, summarize_tool


def _trace(
    trace_id: str,
    scenario: str,
    session: str,
    created: str,
    user: str,
    assistant: str,
    observations: list[dict],
    role: str = "shopper",
    user_id: str = "291",
) -> dict:
    return {
        "id": trace_id,
        "cartwheel_scenario_id": scenario,
        "createdAt": created,
        "input": [{"role": "user", "parts": [{"type": "text", "content": user}]}],
        "output": [{"role": "assistant", "parts": [{"type": "text", "content": assistant}]}],
        "metadata": {
            "attributes": {
                "cartwheel.session_id": session,
                "cartwheel.user_role": role,
                "cartwheel.user_id": user_id,
                "cartwheel.scenario_id": scenario,
            }
        },
        "observations": observations,
    }


def test_sessions_group_turns_and_sort_tools_by_start_time() -> None:
    later = _trace(
        "turn-b",
        "full-094",
        "sess",
        "2026-09-15T23:00:02Z",
        "second question",
        "second reply",
        [],
    )
    earlier = _trace(
        "turn-a",
        "full-094",
        "sess",
        "2026-09-15T23:00:01Z",
        "first question",
        "first reply",
        [
            {
                "type": "TOOL",
                "name": "cancel_order",
                "startTime": "2026-09-15T23:00:01.200Z",
                "input": {"order_id": 1894, "reason": "customer asked"},
                "output": {"ok": True, "order_id": 1894, "status": "cancelled"},
            },
            {
                "type": "TOOL",
                "name": "get_order",
                "startTime": "2026-09-15T23:00:01.100Z",
                "input": {"order_id": 1894},
                "output": {
                    "ok": True,
                    "order": {
                        "order_id": 1894,
                        "status": "placed",
                        "store_name": "Paper Lantern Press",
                    },
                },
            },
            {
                "type": "GENERATION",
                "startTime": "2026-09-15T23:00:01.000Z",
                "input": {
                    "messages": [
                        {
                            "role": "system",
                            "parts": [
                                {
                                    "type": "text",
                                    "content": "You are Cartwheel's support assistant.",
                                }
                            ],
                        }
                    ]
                },
                "output": [
                    {
                        "role": "assistant",
                        "parts": [
                            {"type": "text", "content": "I'll look up the order."},
                            {"type": "tool_call", "name": "get_order"},
                        ],
                    }
                ],
            },
        ],
    )
    other = _trace(
        "turn-c",
        "full-001",
        "other",
        "2026-09-15T22:00:00Z",
        "hello",
        "hi",
        [],
        role="merchant",
        user_id="9008",
    )
    sessions = build_sessions({"traces": [later, earlier, other]})

    assert [page["scenario_id"] for page in sessions] == ["full-001", "full-094"]
    page = sessions[1]
    assert page["role"] == "shopper"
    assert page["user_id"] == "291"
    assert page["system_prompt"].startswith("You are Cartwheel")
    assert [turn["trace_id"] for turn in page["turns"]] == ["turn-a", "turn-b"]
    assert [tool["name"] for tool in page["turns"][0]["tools"]] == [
        "get_order",
        "cancel_order",
    ]
    assert page["turns"][0]["tools"][1]["summary"] == "order 1894 → cancelled"
    assert page["turns"][0]["model_steps"][0]["tools"] == ["get_order"]
    assert page["turns"][0]["model_steps"][0]["text"] == "I'll look up the order."


def test_tool_summaries_keep_the_outcome_visible() -> None:
    assert (
        summarize_tool(
            "issue_refund",
            {"order_id": 2319, "amount_usd": 56.75},
            {
                "ok": True,
                "order_id": 2319,
                "amount_usd": 56.75,
                "status": "auto_approved",
            },
        )
        == "order 2319 · $56.75 · auto_approved"
    )
    denied = summarize_tool(
        "get_order",
        {"order_id": 323},
        {
            "ok": False,
            "error": "permission_denied",
            "reason": "role 'merchant' may not view order #323",
        },
    )
    assert denied.startswith("permission_denied")
    assert (
        summarize_tool(
            "search_help_center",
            {"query": "return window"},
            {"ok": True, "results": [{"policy_id": "cw-returns", "title": "Returns"}]},
        )
        == "“return window” · cw-returns"
    )


def test_real_export_sessions_match_the_review_set() -> None:
    export_path = Path(__file__).resolve().parents[1] / "traces" / "support_traces.json"
    export = json.loads(export_path.read_text())
    sessions = build_sessions(export)
    by_scenario = {page["scenario_id"]: page for page in sessions}

    assert len(sessions) == 100
    assert sum(len(page["turns"]) for page in sessions) == 117
    assert len(by_scenario["full-094"]["turns"]) == 4
    tools = by_scenario["full-069"]["turns"][0]["tools"]
    assert [tool["summary"] for tool in tools] == [
        "order 1894 · placed · Paper Lantern Press",
        "order 1894 → cancelled",
    ]


def test_first_batch_is_thirty_distinct_traces() -> None:
    from analysis.review_app.batch import first_reading_batch, traces_from_sessions

    export_path = Path(__file__).resolve().parents[1] / "traces" / "support_traces.json"
    export = json.loads(export_path.read_text())
    picks = first_reading_batch(traces_from_sessions(build_sessions(export)))
    ids = [pick["trace_id"] for pick in picks]
    assert len(ids) == 30
    assert len(set(ids)) == 30
    assert [pick["batch"] for pick in picks].count("uniform") == 15
    assert [pick["batch"] for pick in picks].count("cluster") == 15
    assert picks[0]["reason"] == "uniform sample"
    assert picks[15]["reason"].startswith("cluster ")


def test_role_batch_covers_each_role_without_reusing_batch_one() -> None:
    from analysis.review_app.batch import first_reading_batch, role_reading_batch, traces_from_sessions

    export_path = Path(__file__).resolve().parents[1] / "traces" / "support_traces.json"
    export = json.loads(export_path.read_text())
    traces = traces_from_sessions(build_sessions(export))
    batch1 = first_reading_batch(traces)
    picks = role_reading_batch(traces, {pick["trace_id"] for pick in batch1})
    ids = [pick["trace_id"] for pick in picks]
    assert len(ids) == 30
    assert len(set(ids)) == 30
    assert not (set(ids) & {pick["trace_id"] for pick in batch1})
    counts = {}
    for pick in picks:
        counts[pick["role"]] = counts.get(pick["role"], 0) + 1
    assert counts == {"shopper": 16, "merchant": 6, "support": 8}
    assert picks[0]["reason"] == "role shopper"
    assert picks[1]["reason"] == "role merchant"
    assert picks[2]["reason"] == "role support"


def test_refund_search_batch_is_twenty_five_unseen_traces() -> None:
    from analysis.review_app.batch import (
        first_reading_batch,
        refund_search_batch,
        role_reading_batch,
        traces_from_sessions,
    )

    export_path = Path(__file__).resolve().parents[1] / "traces" / "support_traces.json"
    export = json.loads(export_path.read_text())
    traces = traces_from_sessions(build_sessions(export))
    batch1 = first_reading_batch(traces)
    batch2 = role_reading_batch(traces, {pick["trace_id"] for pick in batch1})
    reviewed = {pick["trace_id"] for pick in batch1} | {pick["trace_id"] for pick in batch2}
    picks = refund_search_batch(traces, reviewed)
    ids = [pick["trace_id"] for pick in picks]
    assert len(ids) == 25
    assert len(set(ids)) == 25
    assert not (set(ids) & reviewed)
    counts = {}
    for pick in picks:
        counts[pick["reason"]] = counts.get(pick["reason"], 0) + 1
    assert counts == {
        "issue_refund called": 4,
        "reply mentions a refund action": 14,
        "user asked about a refund": 1,
        "refund mentioned, no action words": 6,
    }
    assert picks[0]["reason"] == "issue_refund called"
    assert picks[0]["mode"] == "premature_refund_claim"
