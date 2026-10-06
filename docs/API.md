# API contracts

Local service: `http://127.0.0.1:8000`; interactive schema at `/docs`. All mutating requests require `X-RepoPilot-Client: cli` (browser code uses `browser`). This is **not authentication**. The service rejects cross-origin requests and arbitrary Host headers.

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/health` | Version, mode, actual graph runtime, notices |
| GET/PATCH | `/api/settings` | Operator settings; secret contents never returned |
| GET | `/api/capabilities` | Docker/image and native-graph capability checks |
| POST | `/api/settings/check-model` | Test saved settings or an optional settings PATCH body without saving; sends a fixed greeting, uses the selected adapter, reports latency |
| GET | `/api/workspaces` | Imported workspaces |
| POST | `/api/workspaces/github` | `url`, optional `ref` |
| POST | `/api/workspaces/folder` | Multipart upload; use OpenAPI for field names |
| POST | `/api/workspaces/zip` | ZIP multipart upload |
| POST | `/api/workspaces/text` | `name`, array of `{path,content}` for trusted CLI integrations |
| GET/DELETE | `/api/workspaces/{id}` | Inspect / remove local workspaces and records |
| GET | `/api/workspaces/{id}/versions` | Immutable version history |
| GET | `/api/workspaces/{id}/file` | `path`, optional `snapshot`; indexed files only |
| GET | `/api/workspaces/{id}/export` | Download current filtered source snapshot |
| GET | `/api/workspaces/{id}/symbols` | Optional `q` symbol query |
| POST | `/api/workspaces/{id}/index` | Create optional Embedding index; sends source to configured service |
| GET | `/api/index-jobs/{id}` | Index task status |
| POST | `/api/runs` | `workspace_id`, `question`, optional `session_id`, `intent`, `urls` |
| GET | `/api/runs` | History with `q`, `workspace_id`, `offset`, `limit` |
| GET | `/api/runs/{id}` | Run result and bounded metadata |
| GET | `/api/runs/{id}/checkpoints` | Actual native/portable checkpoint view |
| GET | `/api/runs/{id}/export` | Markdown; `format=json` exports trajectory too |
| POST | `/api/runs/{id}/cancel` | Explicit local cancellation |
| POST | `/api/runs/{id}/resume` | Resume eligible interrupted/error run |
| GET | `/api/events/{id}` | Durable SSE for run/test/index job; `after` or `Last-Event-ID` |
| POST | `/api/markdown` | Restricted Markdown renderer; not arbitrary HTML |
| GET/POST | `/api/proposals` | List or create a precise manual proposal |
| GET | `/api/proposals/{id}` | Diff, digest, status, static checks |
| POST | `/api/proposals/{id}/decision` | `approved` and exact `digest` |
| POST | `/api/proposals/{id}/revert` | Create an inverse proposal, not automatic revert |
| GET | `/api/proposals/{id}/patch` | Unified patch download |
| GET | `/api/proposals/{id}/publish-preview` | Remote preflight and confirmation fingerprint |
| POST | `/api/proposals/{id}/publish` | Exact `fingerprint` and `confirmation="CREATE_DRAFT_PR"` |
| GET/POST | `/api/tests` | List / schedule static or Docker test request |
| GET | `/api/tests/{id}` | Test results and logs |
| POST | `/api/tests/{id}/cancel` | Stop Docker/test task |
| GET | `/api/evaluations` | Genuine run count/latency summary, not fabricated accuracy |

Run terminal states: `done`, `error`, `cancelled`, `interrupted`, `awaiting_approval`. Awaiting approval is **not completed execution**. Test job `done` indicates a recorded result; inspect `result.status` and `result.executed_code` for the actual check/test outcome.

Secret PATCH contract: omit a key to keep it; empty string explicitly deletes. Connection previews follow the same contract and do not persist changes. Updates apply to newly submitted runs; queued and running runs retain their submitted configuration in memory. Credentials are not stored in browser storage.

Offline mode accepts `intent=investigate`; `review` and `change` require an enabled model. Manual proposals and static checks work in either mode.

Detailed field schemas are generated from `repopilot/models.py` in OpenAPI, avoiding a separate stale JSON example collection in the release.
