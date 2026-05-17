---
name: clean-room-runner
description: How to execute README steps inside a Dockerized Python environment. Load this when running extracted commands against a clean container, dealing with long-running servers, or troubleshooting why a runner pass differs from local.
---

# Clean Room Runner Skill

## Container strategy

- Base: `python:<X.Y>-slim-bookworm`, where `<X.Y>` comes from project detection.
- Pre-installed: `git`, `curl`, `ca-certificates`, `build-essential`, and the project's package manager (`poetry` / `uv` / `pipenv` if applicable).
- The repo is bind-mounted at `/workspace`. The container runs `tail -f /dev/null` so it stays alive; each step is executed via `docker exec`.

## Why one long-lived container, not one container per step

Steps share state: `pip install` adds packages, `cp .env.example .env` creates files, `cd subdir` changes cwd. A fresh container per step would lose all of it. The runner persists cwd across `docker exec` calls by appending `echo "__CWD__:$(pwd)"` to each command and parsing the result.

## Image caching

Tag = `readme-truth-checker:<sha256[0:12]>`, hashed over:
- The generated Dockerfile content
- `pyproject.toml`, `requirements.txt`, `Pipfile`, lock files, `setup.py`/`setup.cfg`

Result: first run is slow (full build, 30–90s); subsequent runs reuse the image and start in seconds.

## Per-step execution

| Step kind | How it runs | Default timeout | Pass criterion |
|---|---|---|---|
| Normal | `docker exec bash -lc '<cmd>'` | 180s | exit code 0 |
| Long-running | spawn in background, poll port | 25s readiness | port responds OR process still alive after 5s if no port |

## Port inference order (for long-running steps)

1. Explicit flag: `--port N`, `-p N`
2. `host:N` in the command
3. Framework default by command: Django `runserver` → 8000, Uvicorn → 8000, Flask `run` → 5000, Streamlit → 8501
4. Framework hint from detection (when no command match)
5. None — fall back to "is the process still alive after 5s?"

Readiness probe runs **inside the container** via `curl -fsS http://127.0.0.1:PORT/` — this avoids host-port-publishing complexity and works identically on Linux, Mac, and Windows Docker Desktop.

A 2xx/3xx/4xx response all count as "ready" — a 404 still means the server is up. Only connection failure is treated as not-ready.

## Stop-on-first-failure

When a step fails, the runner does NOT continue. All subsequent steps are recorded with `status: "not_run"` and the reason. This matches reality — a developer following a broken README would also stop at the first failure.

## Hard rules

- Don't sandbox network. `pip install` needs it.
- Don't retry. One run, one verdict.
- Don't run steps marked `skip` (those contain placeholders).
- Don't assume `venv` activation persists. The container IS the isolated environment.
- If `docker info` fails, surface the prerequisite error — do not try to "be helpful" and continue.

## Known limitations (v1)

- `source venv/bin/activate` runs but has no effect across the next `docker exec` (each exec is a new shell). Most quickstarts that use venv inside Docker fail in the same way for the same reason — usually because the test environment doesn't need venv at all.
- Interactive commands (`read`, `npm init`, `git rebase -i`) will time out. There is no way to feed them input.
- Steps that hit external paid APIs will hit them. Do not run this against READMEs whose quickstart calls production.
