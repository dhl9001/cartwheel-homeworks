"""Complete agent tests on evaluation cases, with k runs for each case.

Each evaluation case runs EVAL_K times, each run in a freshly re-seeded world
(the sandbox reset), scored by the case's code checks plus its pinned
Module 2 judges. The per-case pass count feeds the CI rule you
implemented in tests/eval/passk.py:

  - a regression case blocks on ANY failed run (its baseline is k of k);
  - a capability case never blocks CI, but its pass rate is reported in the
    log and exported as a Module 5 improvement target.

The CI decision never uses pass@k. Passing a case because one run in k
succeeded would allow a broken behavior to merge.

The complete agent tests use model calls, so they run on pull
requests and nightly (see .github/workflows/evals.yml), not on every
commit. A local run requires CARTWHEEL_RUN_E2E=1 and an API key. Reruns are
for infrastructure failures only; a verdict flip is data and lands in the
per-case pass rate.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from harbor_adapter.export import load_export_cases
from replay.rollout import (
    apply_checks,
    fresh_world,
    judge_reply,
    load_frozen_judge,
    retrieved_docs_text,
    run_case,
)
from tests.eval.conftest import EVAL_K, PINNED_AGENT_MODEL

pytestmark = pytest.mark.skipif(
    os.environ.get("CARTWHEEL_RUN_E2E") != "1"
    or not os.environ.get("OPENAI_API_KEY"),
    reason=(
        "complete agent tests need CARTWHEEL_RUN_E2E=1 and an API key; "
        "CI runs it on pull requests"
    ),
)

# Judges run when the judge model's key is present; otherwise the run is
# scored by code checks alone (and says so), because a missing key must not
# silently pass a judge-guarded case as green-by-default.
JUDGE_KEY_PRESENT = bool(os.environ.get("ANTHROPIC_API_KEY"))

CASES = [
    case
    for case in load_export_cases()
    if case.get("kind") in {"regression", "capability"}
]


def _run_once(case: dict, root: Path) -> tuple[bool, list[str], dict]:
    """One rollout in a fresh world: (passed, failure_modes, usage)."""
    with fresh_world(root) as db_path:
        transcript = run_case(case, model=PINNED_AGENT_MODEL)
        outcome = apply_checks(case, transcript, db_path)
        failures = list(outcome["failed"])
        judges = case["expected"].get("judges", {})
        if judges and JUDGE_KEY_PRESENT:
            docs = retrieved_docs_text(transcript)
            for mode, expected in judges.items():
                judge = load_frozen_judge(mode)
                verdict = judge_reply(judge, transcript["final_reply"], docs)
                if verdict != expected:
                    failures.append(f"judge:{mode} said {verdict}, expected {expected}")
        elif judges and not JUDGE_KEY_PRESENT:
            failures.append("judge-skipped: no ANTHROPIC_API_KEY (score incomplete)")
    return (not failures, failures, transcript["usage"])


@pytest.mark.parametrize(
    "case",
    CASES or [None],
    ids=[case["id"] for case in CASES] or ["pending"],
)
def test_e2e_case(case: dict | None, tmp_path: Path) -> None:
    if case is None:
        pytest.skip("no classified evaluation cases yet")
    from tests.eval.passk import case_passes

    passes = 0
    failure_lines: list[str] = []
    tokens_in = tokens_out = 0
    for i in range(EVAL_K):
        passed, failures, usage = _run_once(case, tmp_path / f"run-{i}")
        passes += passed
        tokens_in += usage["input_tokens"]
        tokens_out += usage["output_tokens"]
        if not passed:
            failure_lines.append(f"run {i}: {'; '.join(failures)}")

    rate = passes / EVAL_K
    print(
        f"{case['id']} [{case['kind']}] "
        f"pass rate {passes}/{EVAL_K} = {rate:.2f} | "
        f"tokens in {tokens_in} out {tokens_out}"
        + (f" | {failure_lines[0]}" if failure_lines else "")
    )

    decision = case_passes(
        case["kind"], passes, EVAL_K, case.get("baseline_pass_rate")
    )
    assert decision["decision"] == "pass", (
        f"{case['id']} failed CI: {decision['reason']}\n" + "\n".join(failure_lines)
    )
