"""Prepare Homework 5 judge inputs and split human labels.

The judge sees the conversation and the tool activity. It does not see
review notes, failure labels, or scenario metadata.
"""

from __future__ import annotations

import json
from pathlib import Path

import os

from analysis.helpers.guards import is_frozen
from analysis.helpers.tools import (
    _load_judge,
    _load_labels,
    freeze_judge,
    judge_alignment,
    register_judge,
    run_judge,
    split_labels,
)
from analysis.review_app.sessions import build_sessions

ROOT = Path(__file__).resolve().parents[1]
EXPORT_PATH = ROOT / "traces" / "support_traces.json"
INPUTS_PATH = ROOT / "analysis" / "state" / "hw5_trace_inputs.json"
MODE = "unused_tool_call"


def _turn_messages(turn: dict) -> list[dict]:
    """User request, chronological tool calls and results, then the reply."""
    messages: list[dict] = []
    if turn.get("user_text"):
        messages.append({"role": "user", "text": turn["user_text"]})
    for tool in turn.get("tools") or []:
        messages.append(
            {
                "role": "tool_call",
                "name": tool.get("name"),
                "arguments": tool.get("arguments"),
            }
        )
        messages.append(
            {
                "role": "tool_result",
                "name": tool.get("name"),
                "content": tool.get("result"),
            }
        )
    if turn.get("assistant_text"):
        messages.append({"role": "assistant", "text": turn["assistant_text"]})
    return messages


def prepare_inputs(mode: str = MODE) -> list[dict]:
    """Save one judge record for every live label of ``mode``.

    A record includes earlier turns in the same session, then the labeled
    turn. Later turns are omitted. The saved file is the input for every
    prompt version.
    """
    labels = _load_labels(mode)
    if not labels:
        raise ValueError(f"no labels for mode '{mode}'")
    export = json.loads(EXPORT_PATH.read_text(encoding="utf-8"))
    sessions = build_sessions(export)
    located: dict[str, tuple[dict, int]] = {}
    for session in sessions:
        for index, turn in enumerate(session["turns"]):
            located[turn["trace_id"]] = (session, index)

    records: list[dict] = []
    missing: list[str] = []
    multi_turn = 0
    for row in sorted(labels, key=lambda item: item["trace_id"]):
        trace_id = str(row["trace_id"])
        found = located.get(trace_id)
        if found is None:
            missing.append(trace_id)
            continue
        session, index = found
        if index:
            multi_turn += 1
        messages: list[dict] = []
        for turn in session["turns"][: index + 1]:
            messages.extend(_turn_messages(turn))
        records.append({"trace_id": trace_id, "trace": messages})

    if missing:
        raise ValueError(f"{len(missing)} labeled traces are missing from the export")
    if len(records) != len({record["trace_id"] for record in records}):
        raise ValueError("judge inputs contain duplicate trace ids")

    INPUTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    INPUTS_PATH.write_text(json.dumps(records, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {len(records)} inputs to {INPUTS_PATH}")
    print(f"records that include an earlier turn: {multi_turn}")
    return records


def split_data(mode: str = MODE) -> dict[str, list[str]]:
    """Split labels 20/40/40 once, using only traces present in the judge inputs."""
    records = json.loads(INPUTS_PATH.read_text(encoding="utf-8"))
    eligible = [record["trace_id"] for record in records]
    labeled = {str(row["trace_id"]) for row in _load_labels(mode)}
    missing = sorted(labeled - set(eligible))
    if missing:
        raise ValueError(f"{len(missing)} labels have no judge input")
    splits = split_labels(
        mode,
        fractions=(0.20, 0.40, 0.40),
        seed=7,
        min_per_class=10,
        eligible_trace_ids=eligible,
    )
    return splits


def _load_local_env() -> None:
    """Load KEY=VALUE lines from .env. Existing variables win. Values stay unprinted."""
    env_path = ROOT / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip("'\""))


def _draft_id(mode: str, prompt_text: str, judge_model: str) -> str | None:
    """Reuse an unfrozen judge when a rerun would otherwise register a new version."""
    history_path = ROOT / "analysis" / "state" / "judges" / f"_history_{mode}.json"
    if not history_path.exists():
        return None
    history = json.loads(history_path.read_text(encoding="utf-8"))
    versions = history.get("versions") or []
    if not versions:
        return None
    judge_id = versions[-1]["judge_id"]
    judge = _load_judge(judge_id)
    if (
        judge.get("prompt_text") == prompt_text
        and judge.get("model") == judge_model
        and judge.get("status") != "frozen"
    ):
        return judge_id
    return None


def run_development(mode: str = MODE, prompt_path: Path | None = None) -> dict:
    """Register the prompt, score development traces, and save the metrics."""
    _load_local_env()
    os.environ["CARTWHEEL_JUDGE_TRACE_SOURCE"] = str(INPUTS_PATH.resolve())
    path = Path(prompt_path) if prompt_path else ROOT / "prompts" / f"{mode}-v0.txt"
    if not path.is_file():
        path = ROOT / "analysis" / "prompts" / f"{mode}-v0.txt"
    prompt_text = path.read_text(encoding="utf-8")
    judge_model = "gpt-4o-mini"
    judge_id = _draft_id(mode, prompt_text, judge_model)
    if judge_id is None:
        record = register_judge(mode=mode, prompt_text=prompt_text, judge_model=judge_model)
        judge_id = record["judge_id"]
    dev_ids = json.loads((ROOT / "analysis" / "state" / "splits.json").read_text())[mode]["dev"]
    print(f"judge {judge_id} model {judge_model} development traces {len(dev_ids)}")
    run_judge(judge_id, split="dev")
    development = judge_alignment(judge_id, split="dev")
    report_dir = ROOT / "analysis" / "report"
    report_dir.mkdir(parents=True, exist_ok=True)
    report_path = report_dir / f"dev-{judge_id}.json"
    report_path.write_text(json.dumps(development, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {report_path}")
    return development


def run_test(judge_id: str) -> dict:
    """Freeze the selected judge once, score the test split, and save the metrics."""
    _load_local_env()
    os.environ["CARTWHEEL_JUDGE_TRACE_SOURCE"] = str(INPUTS_PATH.resolve())
    judge = _load_judge(judge_id)
    if not is_frozen(judge):
        freeze_judge(judge_id)
    test_ids = json.loads((ROOT / "analysis" / "state" / "splits.json").read_text())[judge["mode"]]["test"]
    print(f"judge {judge_id} model {judge['model']} test traces {len(test_ids)}")
    run_judge(judge_id, split="test")
    test = judge_alignment(judge_id, split="test")
    report_dir = ROOT / "analysis" / "report"
    report_dir.mkdir(parents=True, exist_ok=True)
    report_path = report_dir / f"test-{judge_id}.json"
    report_path.write_text(json.dumps(test, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {report_path}")
    return test


def _class_counts(mode: str, splits: dict[str, list[str]]) -> None:
    """Print Pass and Fail counts. Pass is stored as failure-absent (0)."""
    labels = {str(row["trace_id"]): int(row["label"]) for row in _load_labels(mode)}
    print(f"{'split':<8} {'Pass':>6} {'Fail':>6} {'total':>6}")
    for name in ("train", "dev", "test"):
        ids = splits[name]
        fail = sum(1 for trace_id in ids if labels[trace_id] == 1)
        passed = sum(1 for trace_id in ids if labels[trace_id] == 0)
        print(f"{name:<8} {passed:6} {fail:6} {len(ids):6}")


if __name__ == "__main__":
    import sys

    command = sys.argv[1] if len(sys.argv) > 1 else "prepare"
    if command == "prepare":
        prepare_inputs()
    elif command == "split":
        assignment = split_data()
        _class_counts(MODE, assignment)
    elif command == "dev":
        run_development()
    else:
        raise SystemExit(f"unknown command {command}")
