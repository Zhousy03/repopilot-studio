# Bring your own evaluation data

The release contains **no seeded task dataset or demo repository**. Use real labelled cases from a repository you are allowed to process. Keep private tasks and source outside the public source tree.

## Retrieval

Create UTF-8 JSONL records with `question: string` and `relevant_paths: nonempty string[]`. Paths must exist in the selected immutable snapshot. Run:

```bash
python scripts/evaluate_retrieval.py --workspace YOUR_WORKSPACE_ID --dataset /path/to/private-labels.jsonl --k 5 --output /path/to/retrieval-results.json
```

This CLI performs lexical search without a model. It reports file-level Recall@K and Hit@K after retrieving K chunks. Duplicate chunks from one file do not inflate relevant-file counts. It is not semantic-index evaluation or answer accuracy.

## Agent

JSONL records require `question`; optionally `required_tools`, `forbidden_tools`, `max_tool_calls`, and `required_substrings`. Allowed read-tool names are `search`, `read_file`, `symbols`, `read_issue`, `read_docs`.

```bash
python scripts/evaluate_agent.py --workspace YOUR_WORKSPACE_ID --dataset /path/to/private-agent-labels.jsonl --repeat 3 --output /path/to/agent-results.json --allow-model-cost
```

Start the local API first. In AI mode this sends real requests and costs model tokens; `--allow-model-cost` is mandatory then. Every case gets a new session; evaluator requests are read-only, never approve edits or publish PRs. Errors remain visible in summary; missing labels do not become a fake score. Required substring matching is labelled smoke testing, not semantic correctness.

Read exported trajectories for actual tool arguments, evidence and budgets. Calibrate subjective judgements with independent human labels before claiming quality improvements. The application does not ship a fake comparison table or fabricated LangSmith experiment.
