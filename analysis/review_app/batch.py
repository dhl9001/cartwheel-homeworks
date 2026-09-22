"""Choose the first Homework 4 reading batch.

Batch 1 is 15 uniformly sampled traces plus 15 cluster representatives.
The two lists do not overlap. Clustering uses trace shape (tool count,
distinct tools, retrieval, length). It does not use whether the reply
looks wrong, so the sample is not a search for failures.

The distance and k-means steps follow ``analysis/helpers/selection.py``.
This file does not import that package, because the package init loads
the judge stack.
"""

from __future__ import annotations

import math
import random
import re
from typing import Any

_REFUND_ACTION = re.compile(
    r"\b(?:going to|request|processed|processing|complete|completed|approved|issued|submitting)\b",
    re.IGNORECASE,
)

N_UNIFORM = 15
N_CLUSTER = 15
N_CLUSTERS = 8
_FEATURES = ("turn_count", "tool_call_count", "distinct_tools", "has_retrieval", "tokens")


def traces_from_sessions(sessions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One clustering record per user turn."""
    rows: list[dict[str, Any]] = []
    for session in sessions:
        for turn in session["turns"]:
            names = [tool["name"] for tool in turn.get("tools") or []]
            text = f"{turn.get('user_text') or ''}\n{turn.get('assistant_text') or ''}"
            rows.append(
                {
                    "id": turn["trace_id"],
                    "user_text": turn.get("user_text") or "",
                    "assistant_text": turn.get("assistant_text") or "",
                    "tool_names": names,
                    "meta": {
                        "scenario_id": session.get("scenario_id") or "",
                        "role": session.get("role") or "",
                    },
                    "features": {
                        "turn_count": 1,
                        "tool_call_count": len(names),
                        "distinct_tools": len(set(names)),
                        "has_retrieval": int(
                            any(name in {"search_help_center", "get_policy"} for name in names)
                        ),
                        "tokens": len(text.split()),
                    },
                }
            )
    return rows


def _vector(trace: dict[str, Any]) -> list[float]:
    features = trace.get("features") or {}
    return [float(features.get(name, 0)) for name in _FEATURES]


def _standardize(vectors: list[list[float]]) -> list[list[float]]:
    if not vectors:
        return vectors
    width = len(vectors[0])
    means = [sum(vector[dim] for vector in vectors) / len(vectors) for dim in range(width)]
    stds: list[float] = []
    for dim in range(width):
        var = sum((vector[dim] - means[dim]) ** 2 for vector in vectors) / len(vectors)
        stds.append(math.sqrt(var) or 1.0)
    return [
        [(vector[dim] - means[dim]) / stds[dim] for dim in range(width)]
        for vector in vectors
    ]


def _kmeans(vectors: list[list[float]], k: int, seed: int = 7, iters: int = 25) -> list[int]:
    if not vectors:
        return []
    k = min(k, len(vectors))
    rng = random.Random(seed)
    centroids = [vectors[index][:] for index in rng.sample(range(len(vectors)), k)]
    assign = [0] * len(vectors)
    for _ in range(iters):
        changed = False
        for index, vector in enumerate(vectors):
            best, best_d = 0, float("inf")
            for cluster, center in enumerate(centroids):
                dist = sum((a - b) ** 2 for a, b in zip(vector, center))
                if dist < best_d:
                    best, best_d = cluster, dist
            if assign[index] != best:
                assign[index] = best
                changed = True
        for cluster in range(k):
            members = [vectors[index] for index in range(len(vectors)) if assign[index] == cluster]
            if members:
                centroids[cluster] = [
                    sum(member[dim] for member in members) / len(members)
                    for dim in range(len(members[0]))
                ]
        if not changed:
            break
    return assign


def _distance(left: list[float], right: list[float]) -> float:
    return sum((a - b) ** 2 for a, b in zip(left, right))


def cluster_representatives(
    traces: list[dict[str, Any]],
    k: int = N_CLUSTER,
    exclude_ids: set[str] | None = None,
    n_clusters: int = N_CLUSTERS,
    seed: int = 7,
) -> list[dict[str, str]]:
    """Return traces closest to each cluster center, spread across clusters."""
    excluded = exclude_ids or set()
    pool = [trace for trace in traces if trace.get("id") not in excluded]
    if not pool or k <= 0:
        return []
    vectors = _standardize([_vector(trace) for trace in pool])
    assign = _kmeans(vectors, k=min(n_clusters, len(pool)), seed=seed)
    width = len(vectors[0])
    centers: list[list[float] | None] = []
    for cluster in range(max(assign) + 1):
        members = [vectors[index] for index, label in enumerate(assign) if label == cluster]
        if not members:
            centers.append(None)
            continue
        centers.append(
            [sum(member[dim] for member in members) / len(members) for dim in range(width)]
        )

    ranked: dict[int, list[int]] = {}
    for cluster, center in enumerate(centers):
        if center is None:
            continue
        members = [index for index, label in enumerate(assign) if label == cluster]
        members.sort(key=lambda index: _distance(vectors[index], center))
        ranked[cluster] = members

    picks: list[dict[str, str]] = []
    depth = 0
    while len(picks) < k and any(depth < len(members) for members in ranked.values()):
        for cluster in sorted(ranked):
            if len(picks) >= k:
                break
            members = ranked[cluster]
            if depth >= len(members):
                continue
            trace = pool[members[depth]]
            picks.append(
                {
                    "trace_id": trace["id"],
                    "reason": f"cluster {cluster} representative",
                    "batch": "cluster",
                }
            )
        depth += 1
    return picks


def first_reading_batch(traces: list[dict[str, Any]], seed: int = 7) -> list[dict[str, str]]:
    """15 uniform traces, then 15 cluster representatives from what remains."""
    rng = random.Random(seed)
    uniform = rng.sample(traces, min(N_UNIFORM, len(traces)))
    uniform_ids = {trace["id"] for trace in uniform}
    picks: list[dict[str, str]] = [
        {
            "trace_id": trace["id"],
            "scenario_id": (trace.get("meta") or {}).get("scenario_id") or "",
            "reason": "uniform sample",
            "batch": "uniform",
        }
        for trace in uniform
    ]
    for pick in cluster_representatives(traces, exclude_ids=uniform_ids, seed=seed):
        trace = next(item for item in traces if item["id"] == pick["trace_id"])
        picks.append(
            {
                **pick,
                "scenario_id": (trace.get("meta") or {}).get("scenario_id") or "",
            }
        )
    return picks


def role_reading_batch(
    traces: list[dict[str, Any]],
    exclude_ids: set[str],
    seed: int = 7,
    shopper_n: int = 16,
) -> list[dict[str, str]]:
    """30 traces stratified by user role, excluding traces already reviewed.

    Merchant and support traces left after the exclusion are all included.
    The shopper fill is a uniform draw. Reading order alternates roles.
    """
    excluded = set(exclude_ids)
    groups: dict[str, list[dict[str, Any]]] = {"shopper": [], "merchant": [], "support": []}
    for trace in traces:
        if trace.get("id") in excluded:
            continue
        role = str((trace.get("meta") or {}).get("role") or "")
        if role in groups:
            groups[role].append(trace)
    for role, members in groups.items():
        members.sort(key=lambda trace: str((trace.get("meta") or {}).get("scenario_id") or ""))
    if len(groups["shopper"]) < shopper_n:
        raise ValueError(f"need {shopper_n} shopper traces, found {len(groups['shopper'])}")
    rng = random.Random(seed)
    chosen = {
        "shopper": rng.sample(groups["shopper"], shopper_n),
        "merchant": list(groups["merchant"]),
        "support": list(groups["support"]),
    }
    for members in chosen.values():
        members.sort(key=lambda trace: str((trace.get("meta") or {}).get("scenario_id") or ""))
    picks: list[dict[str, str]] = []
    while any(chosen.values()):
        for role in ("shopper", "merchant", "support"):
            if not chosen[role]:
                continue
            trace = chosen[role].pop(0)
            picks.append(
                {
                    "trace_id": trace["id"],
                    "scenario_id": (trace.get("meta") or {}).get("scenario_id") or "",
                    "reason": f"role {role}",
                    "batch": "role",
                    "review_batch": "2",
                    "role": role,
                }
            )
    return picks


def _scenario(trace: dict[str, Any]) -> str:
    return str((trace.get("meta") or {}).get("scenario_id") or "")


def _refund_pick(trace: dict[str, Any], reason: str) -> dict[str, str]:
    return {
        "trace_id": trace["id"],
        "scenario_id": _scenario(trace),
        "reason": reason,
        "batch": "refund_search",
        "review_batch": "3",
        "mode": "premature_refund_claim",
    }


def refund_search_batch(
    traces: list[dict[str, Any]],
    exclude_ids: set[str],
    seed: int = 7,
    boundary_n: int = 6,
) -> list[dict[str, str]]:
    """25 unread traces retrieved for premature_refund_claim.

    The slices are a deterministic filter, not a label. issue_refund traces
    come first, then replies that pair "refund" with an action word, then a
    user refund question, then a uniform draw of other refund mentions.
    """
    excluded = set(exclude_ids)
    tool: list[dict[str, Any]] = []
    claim: list[dict[str, Any]] = []
    asked: list[dict[str, Any]] = []
    quiet: list[dict[str, Any]] = []
    for trace in traces:
        if trace.get("id") in excluded:
            continue
        user = str(trace.get("user_text") or "")
        assistant = str(trace.get("assistant_text") or "")
        tools = trace.get("tool_names") or []
        mentions = "refund" in f"{user}\n{assistant}".lower()
        if "issue_refund" in tools:
            tool.append(trace)
        elif "refund" in assistant.lower() and _REFUND_ACTION.search(assistant):
            claim.append(trace)
        elif "refund" in user.lower():
            asked.append(trace)
        elif mentions:
            quiet.append(trace)
    for group in (tool, claim, asked, quiet):
        group.sort(key=_scenario)
    if len(quiet) < boundary_n:
        raise ValueError(f"need {boundary_n} boundary traces, found {len(quiet)}")
    boundary = random.Random(seed).sample(quiet, boundary_n)
    boundary.sort(key=_scenario)
    picks: list[dict[str, str]] = []
    picks.extend(_refund_pick(trace, "issue_refund called") for trace in tool)
    picks.extend(_refund_pick(trace, "reply mentions a refund action") for trace in claim)
    picks.extend(_refund_pick(trace, "user asked about a refund") for trace in asked)
    picks.extend(_refund_pick(trace, "refund mentioned, no action words") for trace in boundary)
    return picks


def uniform_stability_batch(
    traces: list[dict[str, Any]],
    exclude_ids: set[str],
    k: int = 15,
    seed: int = 7,
) -> list[dict[str, str]]:
    """15 uniform traces from whatever the earlier batches did not use.

    This is the stability check after a taxonomy draft. The draw does not
    prefer traces that look like a known failure.
    """
    excluded = set(exclude_ids)
    pool = [trace for trace in traces if trace.get("id") not in excluded]
    if len(pool) < k:
        raise ValueError(f"need {k} unread traces, found {len(pool)}")
    chosen = random.Random(seed).sample(pool, k)
    return [
        {
            "trace_id": trace["id"],
            "scenario_id": _scenario(trace),
            "reason": "uniform sample",
            "batch": "uniform",
            "review_batch": "4",
        }
        for trace in chosen
    ]
