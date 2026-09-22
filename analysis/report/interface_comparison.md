# Interface comparison

The reading screen is `analysis/review_app/`. The reference in `analysis/ui/index.html` was the starting point. These three notes come from reading five live Langfuse traces (`full-094`, `full-089`, `full-100`, `full-069`, `full-007`) before the screen was built.

## Retained from the reference

Role is still color, and only color: user blue, assistant green, tool amber. A tool call and its result share one block. A selected sentence becomes a margin note. An agent suggestion uses a dashed mark and separate Accept and Reject controls, so it cannot be mistaken for a note you wrote.

## Changed after inspecting the traces

Langfuse stores one user turn per trace, and the standard view hides the reply inside model spans. The review screen shows one session as a single scroll, in time order. Each tool row states the outcome without an extra click (`order 1894 → cancelled`). Scenario id, role, and user id stay in the header. Model steps stay closed until opened. Resource attributes are omitted.

## Limitation

There is no cluster map. Batch 1 is chosen by a uniform sample and by clustering on trace shape, then opened from the progress list. You cannot click a point in a scatter plot to jump to a trace.
