# 🚀 readme-truth-checker v1.0.0 — initial release

A Claude Code plugin that catches README drift before your next hire does.

It reads the `README.md` at the root of a **Python** project, extracts every shell command from its fenced code blocks, runs them in order inside a fresh Docker container, and reports which steps no longer work. When something is broken, it inspects your actual code to diagnose *why* and proposes a fix. With your approval, it opens a PR.

No more "works on my machine."

---

## ✨ Highlights

- **End-to-end pipeline**: parse → detect → run → diagnose → fix → re-verify → PR
- **Clean-room execution** in a cached Docker image keyed by content hash — no host pollution
- **Pattern-based root-cause diagnosis** with file:line evidence citations from your real codebase
- **PR-ready fixes** generated as in-memory diffs; nothing touches your working tree until you say YES
- **Re-verification gate**: PRs are only opened after the proposed fix runs green in a fresh container

---

## 📦 What's in v1.0

### Pipeline stages

| Stage | Script | Output |
|---|---|---|
| Parse | `scripts/extract_steps.py` | Ordered shell steps + placeholder/long-running classification |
| Detect | `scripts/detect_python_project.py` | Project fingerprint: package manager, Python version, framework hints |
| Run | `scripts/runner.py` | Pass/fail/skipped verdict per step, captured in a clean Docker container |
| Diagnose | `scripts/diagnose.py` | Root-cause report with codebase evidence |
| Fix | `scripts/generate_fix.py` | Patched `README.md` + unified diff (in-memory) |
| Open PR | `scripts/open_pr.py` | Branch + PR via `gh`, only after a green re-verification |

### Project shapes auto-detected

poetry · uv · pipenv · pip + requirements.txt · setup.py

### Drift patterns detected

- Missing env vars (`os.environ["DATABASE_URL"]` not mentioned in README)
- Wrong Python version (README says 3.7+, `pyproject.toml` says `>=3.11`)
- Wrong port (README links `localhost:3000`, FastAPI defaults to 8000)
- Stale install command (`pip install -r requirements.txt` after move to `pyproject.toml`)
- Missing migration step (Django README skips `manage.py migrate`)
- Renamed entry point (script renamed in a recent commit)
- Missing Python module (`ModuleNotFoundError` traced to omitted dependency)
- Non-portable command (`py`, Docker Compose v1) — rewritten to portable equivalents

### Bundled artifacts

- `/readme-check` slash command
- `readme-validator` agent
- `readme-parser`, `clean-room-runner`, `drift-diagnoser` skills
- Local marketplace manifest (`.claude-plugin/marketplace.json`)
- Self-tests with broken-README fixtures for each drift pattern
- Full setup guide (`plugin-setup.md`) and architecture diagram (`README.md`)

---

## 🛠 Installation

**Requirements:** Python 3.11+, Docker (running), Claude Code. `gh` CLI optional, only for auto-PR creation.

Inside a Claude Code session:

```
/plugin marketplace add path/to/readme-truth-checker
/plugin install readme-truth-checker@readme-truth-checker-local
```

Then in any Python repo:

```
/readme-check
```

See [`plugin-setup.md`](./plugin-setup.md) for the full step-by-step guide and troubleshooting.

---

## 🚧 Known limitations (v1.0)

- **Python only.** Node, Go, Rust, etc. coming in future versions.
- **Root `README.md` only** — `docs/`, `CONTRIBUTING.md`, and wikis are not scanned.
- **Pattern-based diagnosis**, not LLM-reasoned. Failure shapes outside the catalogue are reported honestly but not auto-fixed.
- **Fixes are scoped to the README itself** — the plugin never edits source code, `pyproject.toml`, or CI config to make the README's claims true.
- **Stops on first failure.** Later steps are reported as not-run, not green.
- **No interactive prompts.** Steps that expect keyboard input will time out (180s default).
- Steps with placeholders (`<your-token>`) are skipped and flagged for human review.
- Steps that hit paid external APIs will hit them — don't point this at a README whose quickstart calls production.

Full limitations list is in the [README](./README.md#limitations-v10).

---

## 🗺 What's next

- **v1.1** — Custom Dockerfile hooks, `make`/`tox`/`nox` support, tunable diagnosis catalogue, `--continue-on-failure` mode
- **v1.2** — Scan `CONTRIBUTING.md` and configurable extra markdown surfaces
- **v2.0** — Node.js support (npm / pnpm / yarn / bun)
- **v2.x** — Go, Rust, Ruby, Java/Kotlin
- **v3.0** — GitHub Action for continuous README verification on every PR

Roadmap details in the [README](./README.md#roadmap).

---

## 🙏 Acknowledgments

Built with [Claude Code](https://docs.claude.com/en/docs/claude-code). Co-authorship trailer on commits in this release: `Claude Opus 4.7 (1M context) <noreply@anthropic.com>`.

---

**Full changelog:** https://github.com/<your-org>/readme-truth-checker/commits/v1.0.0
