# Plugin Setup Guide

How to install and run the **readme-truth-checker** Claude Code plugin on your local machine.

This guide is for Windows, macOS, and Linux. Pick the commands that match your shell.

---

## 1. Prerequisites

Install these before continuing. The plugin will refuse to run if any are missing.

| Tool | Minimum version | Why it's needed | Check |
|---|---|---|---|
| [Python](https://www.python.org/downloads/) | 3.11 | Runs the extractor, runner, diagnoser, and fix-generator scripts. | `python --version` |
| [Docker Desktop](https://www.docker.com/products/docker-desktop/) (Windows/macOS) or Docker Engine (Linux) | 24.x+ | Hosts the clean room where README steps are executed. Docker **must be running**, not just installed. | `docker info` |
| [Claude Code](https://docs.claude.com/en/docs/claude-code) | latest | The host that loads the plugin and the `/readme-check` command. | `claude --version` |
| [git](https://git-scm.com/downloads) | any recent | Used to clone the plugin and (later) inspect commit history during diagnosis. | `git --version` |
| [GitHub CLI `gh`](https://cli.github.com/) | 2.x | **Optional.** Only needed if you want the plugin to open a pull request with the fix. Run `gh auth login` after install. | `gh --version` |

> If `docker info` errors with "Cannot connect to the Docker daemon," start Docker Desktop (or `sudo systemctl start docker` on Linux) and try again.

---

## 2. Get the plugin source

Clone the repository to a stable location on your machine. Pick a path that won't move — Claude Code will reference it.

**Windows (PowerShell)**
```powershell
git clone https://github.com/<your-org>/readme-truth-checker.git "$env:USERPROFILE\readme-truth-checker"
```

**macOS / Linux**
```bash
git clone https://github.com/<your-org>/readme-truth-checker.git ~/readme-truth-checker
```

Replace `<your-org>` with the actual GitHub org (or use the path you already have it at — for example `D:\readme-truth-checker` on this machine).

---

## 3. Install the plugin in Claude Code

The plugin ships with a local marketplace manifest at `.claude-plugin/marketplace.json`. Installation is a **two-step** flow: first register the marketplace so Claude Code can see the plugin, then install it. **Both commands run inside a Claude Code session** (type them at the `>` prompt).

### Step 3a — Register the local marketplace

Point Claude Code at the cloned directory. This makes the plugin discoverable; it does **not** install it yet.

**Windows (inside a Claude Code session)**
```
/plugin marketplace add C:\Users\<you>\readme-truth-checker
```

**macOS / Linux (inside a Claude Code session)**
```
/plugin marketplace add ~/readme-truth-checker
```

You should see Claude Code confirm: *"Added marketplace `readme-truth-checker-local`."* You can verify with `/plugin marketplace list`.

> The path must be the directory that contains `.claude-plugin/marketplace.json`. If you cloned to a different location, use that path instead.

### Step 3b — Install the plugin from the marketplace

```
/plugin install readme-truth-checker@readme-truth-checker-local
```

The `@readme-truth-checker-local` suffix is the marketplace name from `marketplace.json`. Claude Code will copy the plugin into your local plugin directory and the `/readme-check` slash command becomes available immediately.

---

## 4. Verify the install

1. Open a new Claude Code session in any directory.
2. Type `/` at the prompt. You should see `/readme-check` in the slash-command list, with the description *"Validate the root README.md by running its steps in a clean Docker container."*
3. If you don't see it, run `/plugin` to list installed plugins and confirm `readme-truth-checker` appears.

---

## 5. First run

Navigate to a **Python** project that has a `README.md` at its root, then in Claude Code:

```
/readme-check
```

Or run against a path:

```
/readme-check /path/to/some/python/repo
```

What happens on the first run:

1. **Phase 0** — prerequisites are re-checked.
2. **Phase 1–2** — steps are extracted from the README; the project is fingerprinted (poetry / uv / pipenv / pip / setup.py).
3. **Phase 3** — a Docker image is built. **Expect 2–5 minutes the first time** while the base Python image is pulled and your project's dependencies install. Subsequent runs use the cached image and are fast.
4. **Phase 4+** — results are reported. If everything passes you get a green check. If something failed, you'll see a diagnosis and a proposed README diff. Nothing is written to your working tree until you explicitly approve.

---

## 6. Optional — enable auto-PR creation

Only needed if you want the plugin to push a branch and open a pull request when you accept its fix.

```bash
gh auth login
# Choose: GitHub.com → HTTPS → Login with a web browser
```

Verify:

```bash
gh auth status
```

Without `gh`, the plugin still produces and shows you the patched README diff — it just won't open the PR for you.

---

## 7. Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `docker: command not found` | Docker not installed or not on PATH. | Install Docker Desktop. On Windows, restart your terminal after install. |
| `Cannot connect to the Docker daemon` | Docker daemon not running. | Start Docker Desktop. On Linux: `sudo systemctl start docker`. |
| `/readme-check` not in the slash menu | Marketplace not registered, or plugin not installed from it. | Run `/plugin marketplace list` to confirm `readme-truth-checker-local` is present, then re-run step 3b. Restart Claude Code if needed. |
| `python: command not found` | Python missing or not on PATH (Windows often installs as `py`). | Reinstall Python 3.11+ and tick "Add Python to PATH," or substitute `py` for `python`. |
| Docker build is extremely slow or fails to pull | Restrictive corporate network / DNS. | Configure Docker's registry mirror or proxy in Docker Desktop → Settings → Resources → Proxies. |
| Plugin reports *"this plugin only supports Python projects in v1"* | Target repo has no `pyproject.toml`, `setup.py`, `requirements.txt`, or `Pipfile` at its root. | Run against a Python project. The plugin does not support Node/Go/Rust yet. |
| Plugin says *"README contains no testable shell steps"* | All fenced code blocks are non-shell (e.g. `python`, `json`) or contain placeholders only. | Add at least one runnable shell block (` ```bash ` or ` ```sh `) to the README. |
| `gh: command not found` when accepting a PR | `gh` CLI not installed. | Install the GitHub CLI and run `gh auth login`, or decline the PR step and apply the diff manually. |

---

## 8. Uninstalling

Run these inside a Claude Code session:

```
/plugin uninstall readme-truth-checker
/plugin marketplace remove readme-truth-checker-local
```

The first removes the plugin; the second forgets the marketplace registration. The cloned source directory can then be deleted separately if you no longer need it.

---

## 9. What's next

- Read [`README.md`](./README.md) for the plugin's scope, detected failure patterns, and architecture diagram.
- The `/readme-check` command is defined in [`commands/readme-check.md`](./commands/readme-check.md) — open it if you want to see exactly what the pipeline does step by step.
