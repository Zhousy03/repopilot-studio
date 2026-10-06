# Validation record

Verified on **2026-10-06**, Windows, Python 3.12.14. This record describes the checked source and the scope of the checks.

| Check | Result | Scope |
|---|---|---|
| Python regression suite | **108 passed, 1 skipped** | Import/filtering, exact changes, approvals, patch application, settings previews, cancellation, streaming, native graph and release packaging |
| Native LangGraph | Passed | SQLite checkpoints, application graph and live model runs |
| Browser interaction | **35 passed** | Chromium over a real Uvicorn HTTP server, native fetch and SSE, temporary data directory |
| Responsive layout | Passed | Desktop, 768 px, 390 px and 320 px; mobile file drawer, workspace selection and source references |
| JavaScript syntax / Python compilation | Passed | `node --check` and `compileall` |
| Live model connectivity | Passed | `agnes-3.0-flash`, both direct HTTP and LangChain adapters |
| Live model investigation | Passed | Temporary two-file Python project, streamed answers and valid source-reference IDs |
| Live AI change proposal | Passed | Exact one-line repair, approval, new snapshot and independently verified file content |
| Public GitHub import | Passed | `octocat/Hello-World`, default branch and extensionless README |
| Python wheel | Passed | Built distribution includes all four frontend assets and excludes runtime data |
| Source release | Hash and archive checks | Public source allowlist, normalized text hashes, common credential-pattern scan, ZIP CRC |
| Fresh README install | Passed | Extracted source ZIP, new Python 3.12 virtual environment, `.[ai]` only, actual Uvicorn startup, frontend assets, offline retrieval, approved edit and static check |

The real model checks used a disposable application data directory. Provider credentials were passed through the test process and were removed with that directory. No provider key or generated investigation database is included in the source release.

## GitHub Actions

The [first published commit's CI run](https://github.com/Zhousy03/repopilot-studio/actions/runs/37436778359) passed all five jobs on 2026-10-06, using GitHub-hosted Ubuntu runners:

- Python 3.11, 3.12 and 3.13: regression tests, compilation and source-manifest verification passed. Python 3.12 reported **108 passed, 1 skipped**; the sandbox image is built in a separate job.
- Browser: **35 checks passed** over a real local HTTP server, with no reported browser errors.
- Docker: built the sandbox image and ran `tests/test_sandbox.py`, **4 passed**. This includes real container execution for the integration fixture.

## Reproduce the local checks

```bash
python -m pip install -e ".[ai,dev,ui-test]"
python -m pytest -q -rs
python scripts/check_ui.py
python scripts/verify_release.py
```

The browser script uses an installed Chromium browser, or Playwright Chromium after `python -m playwright install chromium`. Its connection preview uses a controlled local model endpoint. The separately recorded cloud-model checks used real provider requests.

Browser output goes to `.ui-results/` and is excluded from source packaging. Only the empty-workspace screenshot is included for the README. Regression tests create isolated temporary fixtures.

## Integrations requiring further checks

- **Docker execution:** verified with the small integration fixture in GitHub Actions. Tests for a user's imported project still require that user's Docker daemon and an appropriate test image. The local Windows suite skipped its container integration because this machine lacked the prepared image.
- **Private GitHub and draft PR creation:** request contracts and confirmation logic passed tests. Real credentials, remote branch creation and remote CI were not exercised.
- **Embedding, backup-provider failover and native model tool calling:** protocol and failure paths have local tests; these features were not exercised against the supplied provider.
- **PostgreSQL and macOS:** require verification in those environments. Ubuntu and GitHub Actions were exercised by the linked CI run.

The live model acceptance checks verify a small repair flow. They do not measure general answer accuracy or project-wide test coverage. Source-reference validation checks identifiers; readers still need to assess whether cited source supports an answer.
