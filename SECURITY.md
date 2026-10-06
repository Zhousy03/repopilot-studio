# Security and trust boundary

This is a **single-operator local application**, not a secure public code-execution service. Keep `127.0.0.1`, one process, and do not reverse proxy it to the Internet without designing authentication/authorization and isolated execution infrastructure.

## Source handling

Imports are UTF-8 source copies, not a complete clone or a sandbox boundary. Canonical relative paths, case collisions, file types, sizes, ignored paths and symlinks are checked. Some common secret patterns are redacted before model context. Pattern matching cannot find every secret. Operator source-upload consent is required for AI/Embedding. Original imported source bytes remain locally stored; browser/source exports may expose them to the local operator.

Settings and tokens are stored in a permission-restricted **plaintext local file**, not encrypted. Windows ACL details depend on the user's environment. Back up and protect the data directory accordingly. Do not submit database files, tokens, private source, traces or patches to a public issue.

Remote HTTP requests use the operator's environment proxy and certificate settings (`HTTP_PROXY`, `HTTPS_PROXY`, `NO_PROXY`, `SSL_CERT_FILE`, `SSL_CERT_DIR`). The direct HTTP model adapter bypasses proxies for loopback model addresses. Review those environment settings when using a shared machine. Requests do not automatically forward credentials across redirects.

## Agent permissions

Read actions are allowlisted. Model-generated filenames/arguments are validated. The model cannot execute shell commands, approve a proposal, bypass snapshot checks or publish a PR itself. Prompt instructions about treating documents as untrusted are defence-in-depth, not an absolute prompt-injection solution. Facts are not certified by a citation-ID check.

Exact edits require a valid proposal digest and unchanged base snapshot. Approval affects the local imported copy. Remote publication has a second approval. Network writes can still have ambiguous outcomes; inspect remote state before retrying.

## Code execution

Static parsing is not execution. Dynamic execution is Docker-only, explicit consent, fixed profiles, no network, no host fallback, constrained user/filesystem/resources. Malicious tests can lie about results and can attack the runtime. Containers share the host kernel; use disposable infrastructure for untrusted code. Docker API access itself is powerful. The application container recipe does not mount the Docker socket.

## Operational limits

No public authentication, tenant isolation, OAuth, distributed locks, multi-worker execution, comprehensive secret vault, autonomous CI approval or universal language test runner is implemented. The built-in source/runtime limits are deliberate and are not claims of support for arbitrarily large repositories.

Before public release, run the optional native-graph/Docker/live-provider tests in your environment, inspect dependency versions and update to security fixes, and review GitHub permissions/CI triggers. The included workflow file has not automatically executed merely because it exists in this ZIP.

Report security findings privately to the maintainer of the repository where you publish this code; do not post credentials or exploitable private deployments. No fabricated maintainer contact is supplied in this standalone source package.
