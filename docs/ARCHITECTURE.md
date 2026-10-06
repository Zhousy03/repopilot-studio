# Architecture and contracts

## Layers

- `api.py`: validated requests, local-origin boundary, REST and durable SSE replay. Endpoints do not accept arbitrary filesystem roots or shell commands.
- `service.py`: per-run budgets, task scheduling, cancellation, session context, native/portable executor selection, persist final records.
- `workflow.py`: actual retrieval/planning/read tools, isolated research/review, optional coder, generation, verification and proposal nodes.
- `graph.py`: native `StateGraph` and `interrupt`; no fallback shim pretending to be LangGraph.
- `ai.py`: actual `ChatOpenAI` adapter plus direct HTTP protocol adapter, text streaming, bounded requests, backup provider, usage accounting.
- `workspaces.py`, `security.py`: filtered immutable source snapshots and explicit file access boundaries.
- `retrieval.py`: source chunk metadata and symbols, lexical / optional hybrid ranking. Scores are not answer confidence.
- `changes.py`: exact preconditions, patch previews, approval digest, new snapshots, inverse proposals.
- `github.py`: fixed-host import, issue reads, separate publish confirmation, branch/draft PR journal.
- `sandbox.py`: fixed Docker profiles only; no shell fallback.
- `db.py`: application records/events SQLite. Graph checkpoint storage is a separate concern.

## State and control

Each run is bound to one workspace snapshot. A conversation can supply up to three completed turns from the same session and snapshot; this is not an infinite transcript. A run's read tools are allowlisted and read against that immutable snapshot. A user may supply approved official-document / GitHub Issue URLs. General free-form web browsing is not provided.

Planner uses validated structured output or optional native tool calls. It cannot create a new arbitrary tool name, select another workspace or authorize a write. `max_rounds`, `max_tool_calls`, `max_model_calls`, per-request timeouts and per-run wall time bound the loop. Subagents get compact private inputs; they do not share unrestricted scratchpads. Model-generated correctness is never guaranteed.

A change request produces a proposal first. Native Graph approval uses `interrupt()`; the service verifies and applies the immutable proposal **outside the resumable node** before passing the approval result back. The portable executor labels itself separately and persists its next node in application SQLite. Recovery can replay reads/model requests and incur cost; checkpoints do not make arbitrary external operations exactly-once.

## Events

`POST /api/runs` returns a run ID immediately. `GET /api/events/{id}` streams saved SSE events with monotonic IDs. Reconnect uses `Last-Event-ID` or `after`; client disconnect does not cancel background computation. Explicit cancel is a separate POST.

Events include `started`, `stage`, `plan`, `tool_started`, `tool_finished`, `agent_started`, `agent_finished`, `evidence`, `answer_delta`, `answer_reset`, `first_token`, `retry`, `fallback`, `warning`, `approval_required`, `done`, `cancelled`, `error`. The UI reflects these actual events. It does not expose or fabricate private chain-of-thought.

`answer_delta` is provisional. Final verification checks evidence-ID membership, not semantic entailment. Invalid citations lead to evidence fallback rather than retaining an unsupported model draft as a verified answer.

## Versions and publication

File identity includes path, content hash and snapshot identity. One pending proposal cannot apply over a newer workspace head. Duplicate approval is idempotent. New versions never silently overwrite the originally uploaded directory.

GitHub import records commit/tree/ref. Publishing checks the imported branch head is still unchanged, builds a tree from the original remote tree plus cumulative approved changes, creates a deterministic branch and a draft PR, and journals remote identities. Writes do not receive blind transport retries. No automatic fork or merge. A timeout can be ambiguous and must be reconciled with remote state; absolute exactly-once semantics are not promised.

## Scaling boundary

SQLite app data, in-process locks and tasks mean single process/single operator. PostgreSQL can replace native Graph checkpoint storage but **does not** turn the entire app into a multi-worker/multi-tenant service. Public deployment would require identity, ownership checks, separate executor hosts, a job queue, proper secret management, quotas and audit infrastructure.

Official API references used while developing:

- https://docs.langchain.com/oss/python/langgraph/persistence
- https://docs.langchain.com/oss/python/langgraph/workflows-agents
- https://docs.github.com/en/rest/git/trees
- https://docs.github.com/en/rest/pulls/pulls

A documentation reference supports an API contract; it does not establish that a particular live integration has been tested in this release environment.
