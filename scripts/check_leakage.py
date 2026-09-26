"""The evaluation input leakage check (Module 3, Lecture 1.1).

An evaluation case that also appears as an example in the agent prompt or a judge prompt no longer tests generalization, because the model has seen the input. The evaluation case set therefore lives in separate files, and the continuous integration workflow compares its inputs with prompt content on every pull request.

`find_leaks` is yours to implement. The `main()` wiring below it collects
the course's prompt surfaces (the support agent system prompt and every registered
judge prompt) and exits nonzero on any leak.

Usage:
    uv run python scripts/check_leakage.py
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]


def _normalize(text: str) -> str:
    """Lowercase and collapse all whitespace runs to single spaces (helper,
    provided). Compare normalized text so a reflowed quote still matches."""
    return re.sub(r"\s+", " ", text.lower()).strip()


def find_leaks(
    evaluation_inputs: dict[str, str],
    prompt_texts: dict[str, str],
    min_chars: int = 24,
) -> list[dict[str, Any]]:
    """Find evaluation inputs that appear inside any prompt text.

    The contract, precisely:

      1. Normalize every evaluation input and every prompt text with
         :func:`_normalize` before comparing, so case and whitespace
         differences do not hide a leak.
      2. An evaluation input LEAKS into a prompt when its normalized text appears
         as a substring of the normalized prompt text.
      3. Skip evaluation inputs whose normalized length is under ``min_chars``:
         a very short message ("thanks!") will appear in prose by
         coincidence, and flagging it would train people to ignore the
         check.
      4. Return one record per (evaluation input, prompt) pair that leaks:
         ``{"case_id": ..., "prompt": ..., "excerpt": ...}`` where
         ``excerpt`` is the first 60 characters of the normalized evaluation
         input. Order records by case_id, then by prompt name (both
         ascending), so the report is stable.

    Args:
        evaluation_inputs: case id -> the case's user message (for multi-turn
            cases the caller passes each turn as its own entry, with ids
            like "e-007#1").
        prompt_texts: prompt name (e.g. "agent/agent.py:SYSTEM_PROMPT_TEMPLATE"
            or "judges/unsupported_policy_claim-v3") -> the prompt's text.
        min_chars: minimum normalized length for an evaluation input to be
            checked.

    Returns:
        A list of leak records, empty when the suite is clean.
    """
    leaks: list[dict[str, Any]] = []
    for case_id, evaluation_input in evaluation_inputs.items():
        normalized_input = _normalize(evaluation_input)
        if len(normalized_input) < min_chars:
            continue
        excerpt = normalized_input[:60]
        for prompt_name, prompt_text in prompt_texts.items():
            if normalized_input in _normalize(prompt_text):
                leaks.append(
                    {
                        "case_id": case_id,
                        "prompt": prompt_name,
                        "excerpt": excerpt,
                    }
                )
    leaks.sort(key=lambda leak: (leak["case_id"], leak["prompt"]))
    return leaks


# ---------------------------------------------------------------------------
# The wiring (instructor-provided): collect the course's prompt surfaces and
# evaluation inputs, run find_leaks, exit nonzero on a leak.
# ---------------------------------------------------------------------------


def collect_evaluation_inputs(cases_path: Path | None = None) -> dict[str, str]:
    path = cases_path or REPO_ROOT / "eval_cases" / "cases.jsonl"
    inputs: dict[str, str] = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        case = json.loads(line)
        inputs[case["id"]] = case["input"]["message"]
        for i, followup in enumerate(case["input"].get("followups", []), start=1):
            inputs[f"{case['id']}#{i}"] = followup
    return inputs


def collect_prompt_texts() -> dict[str, str]:
    prompts: dict[str, str] = {}
    from agent.agent import SYSTEM_PROMPT_TEMPLATE

    prompts["agent/agent.py:SYSTEM_PROMPT_TEMPLATE"] = SYSTEM_PROMPT_TEMPLATE
    judges_dir = REPO_ROOT / "analysis" / "state" / "judges"
    if judges_dir.exists():
        for judge_path in sorted(judges_dir.glob("*.json")):
            if judge_path.name.startswith("_history"):
                continue
            judge = json.loads(judge_path.read_text())
            if judge.get("prompt_text"):
                prompts[f"judges/{judge_path.stem}"] = judge["prompt_text"]
    return prompts


def main() -> int:
    leaks = find_leaks(collect_evaluation_inputs(), collect_prompt_texts())
    if not leaks:
        print("leakage check: clean (no evaluation input appears in any prompt)")
        return 0
    print(f"leakage check: {len(leaks)} leak(s) found", file=sys.stderr)
    for leak in leaks:
        print(
            f"  {leak['case_id']} leaks into {leak['prompt']}: "
            f"{leak['excerpt']!r}",
            file=sys.stderr,
        )
    print(
        "An evaluation case quoted in a prompt no longer tests generalization. "
        "Remove it from the prompt or replace the evaluation case.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
