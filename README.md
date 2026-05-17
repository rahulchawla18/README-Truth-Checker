# readme-truth-checker

A Claude Code plugin that catches README drift before your next hire does.

## What it does

Reads the `README.md` at the root of a Python project, extracts every shell command from its fenced code blocks, runs them in order inside a fresh Docker container, and reports which steps no longer work. When something is broken, it inspects your actual code to diagnose *why* and proposes a fix. With your approval, it opens a PR.

It treats your README like a test suite. No more "works on my machine."

## Scope at a glance

| | |
|---|---|
| Current version | v1.0 |
| Ecosystems | Python only |
| Docs scanned | Root `README.md` only |
| Runtime | Local Docker |
| Side effects | None until you say YES to the PR prompt |

## Requirements

- Python 3.11+
- Docker (Docker Desktop on Windows/Mac, or Docker Engine on Linux) — must be running
- `gh` CLI, only if you want auto-PR creation (`gh auth login` required)

See [`plugin-setup.md`](./plugin-setup.md) for a full step-by-step install guide.

## Install (Claude Code plugin)

The plugin ships with its own local marketplace manifest (`.claude-plugin/marketplace.json`), so installation is a two-step flow inside any Claude Code session:

**1. Register the marketplace** (point Claude Code at this repo so it can see the plugin):

```
/plugin marketplace add path/to/readme-truth-checker
```

**2. Install the plugin** from that marketplace:

```
/plugin install readme-truth-checker@readme-truth-checker-local
```

Once installed, `/readme-check` is available in any session. See [`plugin-setup.md`](./plugin-setup.md) for OS-specific paths and troubleshooting.

## Usage

From inside a Python repo:

```
/readme-check
```

Or against a specific path:

```
/readme-check /path/to/some/python/repo
```

The plugin will:

1. Parse `README.md` into ordered steps
2. Detect your Python project shape (poetry / uv / pipenv / pip / setup.py)
3. Build a clean container, run each step, capture results
4. If everything passes: print a green check and exit
5. If anything fails: diagnose root cause, generate a fix, show you the diff
6. Ask you: "Open a PR with these changes?"
   - **YES** → re-verify the fix in a fresh container, then `gh pr create`
   - **NO** → print the suggested diff and exit. No PR, no file changes.

---

## What's in v1.0

The current release ships an end-to-end pipeline that goes from raw `README.md` to a verified pull request without modifying your working tree until you approve.

### Pipeline stages

| Stage | Script | Output |
|---|---|---|
| Parse | `scripts/extract_steps.py` | Ordered list of shell steps + placeholder/long-running classification |
| Detect | `scripts/detect_python_project.py` | Project fingerprint: package manager, Python version, framework hints |
| Run | `scripts/runner.py` | Pass/fail/skipped verdict per step, captured in a clean Docker container |
| Diagnose | `scripts/diagnose.py` | Root-cause report citing real evidence from the codebase |
| Fix | `scripts/generate_fix.py` | Patched `README.md` + unified diff (in-memory) |
| Open PR | `scripts/open_pr.py` | Branch + PR via `gh`, only after a green re-verification |

### Project shapes auto-detected

- **poetry** (`pyproject.toml` with `[tool.poetry]`)
- **uv** (`pyproject.toml` with `[tool.uv]` or `uv.lock`)
- **pipenv** (`Pipfile` / `Pipfile.lock`)
- **pip + requirements.txt**
- **setup.py** (legacy)

### Drift patterns detected

| Failure pattern | Example |
|---|---|
| Missing env var | README runs `python app.py` but code reads `os.environ["DATABASE_URL"]` |
| Wrong Python version | README claims 3.7+ but `pyproject.toml` says `>=3.11` |
| Wrong port | README links `localhost:3000` but FastAPI defaults to 8000 |
| Stale install command | README uses `pip install -r requirements.txt` but project moved to `pyproject.toml` |
| Missing migration step | Django README skips `manage.py migrate` |
| Renamed entry point | README references a script that was renamed in a recent commit |
| Missing Python module | `ModuleNotFoundError` traced to a dependency missing from the install step |
| Non-portable command | `py` (Windows-only launcher), Docker Compose v1 syntax, etc. — rewritten to portable equivalents |

### Safety guarantees

- Read-only against your working tree until you explicitly approve the PR.
- One execution, one verdict — no silent retries that mask flakiness.
- Long-running steps (servers) are started detached, port-polled, then killed; they never block the run.
- Steps with placeholders (`<your-token>`, `YOUR_API_KEY`) are skipped and surfaced for human review rather than guessed at.

---

## Limitations (v1.0)

Things this version explicitly does **not** do — by design or because they're slated for a later release.

### Ecosystem coverage

- **Python only.** Node, Go, Rust, Ruby, Java, etc. are out of scope. The detector will report `is_python: false` and exit on non-Python repos.
- **Root `README.md` only.** Files under `docs/`, `CONTRIBUTING.md`, GitHub wikis, and in-code docstrings are not scanned.
- **Single Dockerfile baseline.** The clean room is always a `python:<version>-slim-bookworm` container. Projects that need system packages beyond `git`, `curl`, `build-essential`, or that need a non-Debian base, will fail to install.

### Step handling

- Steps containing placeholders are **skipped, not solved**. The plugin will flag them; it won't invent secrets.
- `cd` and `export` carry across steps via a cwd/env-persistence trick, but **`venv` activation does not** — the container itself is the isolated environment, so `source venv/bin/activate` is effectively a no-op.
- Steps that hit external paid APIs **will** hit them. Don't point this at a README whose quickstart calls production.
- No interactive prompts. Anything that expects keyboard input (`read`, password prompts, interactive `gh auth login`) will hang until the per-step timeout (default 180s) kills it.
- Stops on the first failing step. Later steps that *might* have worked are reported as not-run, not green.

### Diagnosis and fixes

- Diagnosis is **pattern-based**, not LLM-reasoned. If the failure shape isn't in the catalogue (env var, missing module, wrong port, renamed entry point, portability rewrite, long-running readiness failure), the plugin will report the failure honestly but won't propose a fix.
- Fixes are scoped to **the README itself**. The plugin will never edit `pyproject.toml`, source code, or CI config to make the README's claims true.
- No multi-file rewrites. One README, one diff.

### Runtime

- Requires a **local** Docker daemon. No remote builders, no rootless-podman support tested, no CI-only mode in v1.0.
- First run is slow (2–5 min) because the base image is pulled and your project's dependencies install fresh. Subsequent runs reuse a cached image keyed by a content hash.
- Windows hosts work, but the clean room is always Linux — Windows-specific README steps (e.g. `py -3.11`) will be rewritten or fail.

---

## Roadmap

These are *intended* directions, not commitments. Order and scope will shift based on usage.

### v1.1 — Polish & coverage breadth (Python)

- Custom Dockerfile / `apt-get` hooks for projects that need system packages beyond the v1.0 baseline.
- Better handling of `pre-commit`, `make`-driven setups, and `tox`/`nox` test entry points.
- Cache the diagnosis catalogue patterns externally so they can be tuned without a plugin release.
- Optional `--continue-on-failure` mode that runs every step and aggregates verdicts, useful for first-time audits.

### v1.2 — More docs surfaces

- Scan `CONTRIBUTING.md`, `docs/quickstart.md`, and a configurable list of additional markdown files.
- Per-file verdict reports and a combined fix PR.

### v2.0 — Second ecosystem

- **Node.js** support (npm / pnpm / yarn / bun detection, Node version from `engines` or `.nvmrc`, Node-flavoured drift patterns).
- Shared abstractions extracted so adding the third ecosystem isn't another rewrite.

### v2.x — Additional ecosystems

- Go (modules, `go run`, `go test`)
- Rust (Cargo, `cargo run`, feature flags)
- Ruby (Bundler, Rails-aware migration step detection)
- Java/Kotlin (Gradle / Maven)

### v3.0 — Continuous mode

- GitHub Action that runs `readme-check` on every PR touching `README.md` or files referenced by it.
- Drift budget: warn when a README hasn't been verified in N days.
- Optional LLM-assisted diagnosis fallback when the pattern catalogue can't explain a failure.

### Explicitly **not** on the roadmap

- Editing source files to make stale README claims true. The README is the source of drift; the fix belongs in the README.
- Running steps outside a container on the host. The isolation is a feature, not a limitation.
- A web UI. This is a CLI/Claude Code plugin.

---

## How it works (architecture)

```text
README.md ─► extract_steps.py ─► steps.json
                                     │
                                     ▼
repo ─────► detect_python_project.py ─► project.json
                                     │
                                     ▼
                                  runner.py ─► results.json  (in clean Docker container)
                                     │
                                     ▼
                                  diagnose.py ─► drift_report.json
                                     │
                                     ▼
                                generate_fix.py ─► patched README + diff
                                     │
                          ┌──────────┴──────────┐
                          ▼                     ▼
                  user says YES           user says NO
                          │                     │
                          ▼                     ▼
                  re-verify ─► open_pr.py    print diff, exit
```

## License

MIT
