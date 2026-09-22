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

`full-079` was rejected as a search hit and kept as a close negative. `issue_refund` returned `auto_approved`, and the note on annotation `a1790093957517` was "no failure observed." Reporting a refund the tool has already approved is not this failure. `full-081` is the same shape. `full-007` is the third close negative: `issue_refund` returned `auto_approved` for $56, and the reply reports that status. `full-053` was also rejected: the reply says the dry bag is eligible and under $100, then asks before issuing anything. Nothing is left unsettled.

## Confirmed modes

Each mode has three close negatives, a boundary, and an evaluator. A code evaluator checks a trace field. A judge evaluator reads the reply.

| Mode | Requirement | Evaluator | Close negatives |
| --- | --- | --- | --- |
| `narrated_method` | RESP-7 | judge | full-100, full-091, full-089 |
| `cited_policy_id` | RESP-1 | code | full-079, full-070, full-033 |
| `unasked_facts` | RESP-8 | judge | full-049, full-027, full-069 |
| `unclear_target` | RESP-9 | judge | full-027, full-032, full-094 |
| `unused_tool_call` | RESP-10 | judge | full-069, full-062, full-034 |
| `premature_refund_claim` | RESP-6 | judge | full-079, full-081, full-007 |
| `repeated_fact` | RESP-11 | judge | full-063, full-035, full-038 |
| `escalated_support_user` | ESC-5 | code | full-032, full-034, full-031 |

`cited_policy_id` is a code check for a policy document id in the user-visible reply. `escalated_support_user` is a code check for a support user plus `escalate_to_human`. The other six depend on what the user asked and what the reply claims, so they are judges.

## Specification

The gaps found in the notes are written in `SPEC.md`. The running system prompt was not changed.

`RESP-1` forbids naming a policy document id. The motivating note is `a1790085259356` on `full-007`. `RESP-6` forbids stating refund eligibility while the case is still unsettled. The motivating note is `a1790094260938` on `full-066`. `RESP-7` through `RESP-11` cover narrating the method, unasked facts, an unclear target, an unused tool call, and a repeated fact. `ESC-5` covers opening a ticket for someone who is already support. The motivating notes are `a1790085679260`, `a1790084289463`, `a1790085225281`, `a1790085152749`, `a1790091475675`, and `a1790090517628`.

## AgentDebug

AgentDebug (arXiv:2509.25370) groups failures into memory, reflection, planning, action, and system. `unused_tool_call` is the nearest planning overlap. `premature_refund_claim` is the nearest reflection overlap. The other six modes are about what the reply tells the user, and AgentDebug does not name them. No mode was added from that taxonomy. The reviewed notes do not support a separate memory, action-format, or tool-crash mode. The permission-denial wording on `full-077` stayed in `narrated_method`.

## Records

The same judgments are in `analysis/review_app/state/labels/` and `analysis/state/labels/`. Each judgment is also a numeric Langfuse score on the trace: 1 means the failure is present and 0 means it is absent. The 100-trace sample manifest is `analysis/state/sample_manifest.json`. The taxonomy, notes, and rejected search hits used for this review stay in `analysis/review_app/state/`. The course demo files `analysis/state/patterns.json` and `analysis/state/annotations.json` were left in place because the structural tests read the demo taxonomy.
