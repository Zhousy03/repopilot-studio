# Static checks and isolated execution

## Static means parsing, not running

`static` parses Python with `ast.parse`, JSON with the standard parser and TOML with `tomllib`. A parse success does not test imports, types, business logic or security. Unsupported file types do not silently become a passed test suite; a snapshot with no applicable parsers is labelled `not_applicable`.

## Real execution profiles

The backend launches only the fixed Docker command in `repopilot/sandbox.py`. Repository text never becomes a host shell command.

| UI profile | Command inside trusted container |
|---|---|
| pytest | `python -m pytest -q -p no:cacheprovider` |
| unittest | `python -m unittest discover -s tests -v` |
| node-test | `node --test` |

The container copies the read-only `/input` snapshot to ephemeral `/work` and runs there. It does not mutate saved source snapshots.

Build from the project root:

```bash
docker build -t repopilot-sandbox:1 -f sandbox/Dockerfile .
```

or for the built-in Node test runner:

```bash
docker build -t repopilot-node-sandbox:1 -f sandbox/Dockerfile.node .
```

The **image build** can use the network. The **test invocation** uses `--network=none`, read-only root, dropped capabilities, a non-root UID, a PID limit, 1 CPU, 512 MB memory, bounded tmpfs, runtime timeout and log limits. It never mounts the Docker socket into the test container. The Docker host account still has powerful privileges; do not mistake this for public multi-tenant isolation.

## Project dependencies

The supplied Python image contains Python and pytest only; Node image contains Node and the trusted Python runner. For real applications, prepare a trusted image containing reviewed dependency versions, while retaining `/opt/repopilot_runner.py` and the `python` entrypoint executable. Configure that image in Settings. No automatic `pip install -r requirements.txt`, `npm install`, package lifecycle hook, arbitrary shell command or networked integration test is run by the app.

Import filtering excludes binaries, submodules, ignored files, environments and potentially required assets. Therefore a test failure can mean the imported subset or image is incomplete. A zero test-process exit code is not proof of coverage or correct implementation. For hostile code, use disposable dedicated infrastructure, not a machine holding secrets.

## Local API process recommended for tests

Run the API using local Python with Docker Desktop/Engine available on the same host. The optional top-level `Dockerfile` / `compose.yaml` builds an application container without Docker socket or executor access; dynamic tests are consequently unavailable inside that deployment. We deliberately do not mount the host Docker socket just to make a feature button work.

No Docker means a truthful `infrastructure_error` / unavailable result, not a host fallback.

## Cancellation and logs

The API streams actual stdout/stderr text, bounded to prevent runaway output. Timeout/cancel explicitly removes the named container (`docker rm -f`); killing the Docker client alone is insufficient. Completed test records include the exact snapshot, profile, image, exit code, elapsed time and whether the trusted runner started.

## Application tests

```bash
python -m pip install -e ".[ai,dev]"
python -m pytest -q
```

The tests generate isolated temporary fixtures. Docker integration skips only when daemon/image are missing; native graph integration skips only when optional dependencies are missing. CI recipes are included but must run in your repository before you display a passing badge.

Primary reference: https://docs.docker.com/engine/containers/run/
