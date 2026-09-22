# Review summary

The review set is 100 distinct traces from `traces/support_traces.json` (117 traces, scenarios `full-001` through `full-100`). They cover 86 sessions. Counts below are sample fractions. The batches were chosen by role and by a refund search, so they are not an estimate of how often these failures occur.

## Sample composition

| Batch | Traces | How they were chosen |
| --- | ---: | --- |
| 1 | 30 | 15 uniform draws, then 15 cluster representatives (8 clusters, seed 7) |
| 2 | 30 | Every remaining merchant trace (6) and support trace (8), plus 16 uniform shopper traces |
| 3 | 25 | Refund search: 4 traces that called `issue_refund`, 14 replies that mention a refund action, 1 other trace whose user asked about a refund, and 6 traces that only mention a refund |
| 4 | 15 | Uniform draw from the traces the earlier batches did not use |

Roles in the 100 traces: 76 shopper, 13 merchant, 11 support. Every merchant trace and every support trace in the export is in the set. Seventeen shopper traces were not reviewed.

## Sample fractions

Present means the candidate's failure is in the trace. A trace can be present for more than one candidate.

| Candidate | Present |
| --- | ---: |
| `narrated_method` | 85/100 |
| `cited_policy_id` | 47/100 |
| `unused_tool_call` | 28/100 |
| `unasked_facts` | 17/100 |
| `unclear_target` | 11/100 |
| `premature_refund_claim` | 10/100 |
| `repeated_fact` | 5/100 |
| `escalated_support_user` | 3/100 |

## Stability of the final 15

The last batch added one previously unseen note, on `full-056`: the agent should compare today's date with the ship date, then escalate or tell the user to wait. That note stayed ungrouped. No new mode appeared, so no further batch was reviewed.

## One taxonomy revision

After the refund search, `premature_refund_claim` was narrowed. A reply that says an order is refund-eligible, or that a refund is being submitted, is this failure only while a person still has to approve it, a policy question is still open, or the order record disagrees with the policy.

`full-079` was rejected as a search hit and kept as a close negative. `issue_refund` returned `auto_approved`, and the note on annotation `a1790093957517` was "no failure observed." Reporting a refund the tool has already approved is not this failure. `full-053` was also rejected: the reply says the dry bag is eligible and under $100, then asks before issuing anything. Nothing is left unsettled.

## Specification

Two gaps found in the notes are now written in `SPEC.md`. The running system prompt was not changed.

`RESP-1` now forbids naming a policy document id in a user-visible reply. The motivating note is `a1790085259356` on `full-007`, quote `cw-refunds`, note "Don't cite internal doc name."

`RESP-6` forbids stating refund eligibility, or that a refund is being submitted, while human review, an open policy question, or a disagreeing order record is still unresolved. The motivating note is `a1790094260938` on `full-066`, note "dont confirm eligible if it needs human review first." `RESP-2` still covers claiming success before the tool reports success.

The eight candidates are still candidates. An evaluator type is not chosen yet.

The same judgments are in `analysis/review_app/state/labels/` and `analysis/state/labels/`. They were not written to Langfuse: the `langfuse` package is not installed in `.venv`, and `uv sync` fails while building `cbor2`. The environment variables `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`, and `LANGFUSE_HOST` are set in `.env`.
