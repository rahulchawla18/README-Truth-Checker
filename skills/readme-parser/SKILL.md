---
name: readme-parser
description: Conventions for extracting and classifying shell commands from README markdown. Load this when parsing fenced code blocks, detecting placeholders, distinguishing setup/install/run/test commands, or deciding which README lines are testable.
---

# README Parser Skill

When analyzing a README for the truth checker, follow these rules.

## Which fenced blocks count as shell

Treat these language tags as shell-executable: `bash`, `sh`, `shell`, `console`, `terminal`, `zsh`, `fish`, and untagged code blocks. Skip blocks tagged `python`, `json`, `yaml`, `toml`, `dockerfile`, `text` — they describe, they don't execute.

For `console` / `terminal` blocks, strip these prompt prefixes (they are decoration, not part of the command):

```
$
#
>
PS>
PS C:\>
```

## Multi-line handling

- **Backslash continuations**: a line ending in `\` joins with the next line into a single command.
- **Multiple commands in one block**: each newline-separated line is its own step (unless joined by `\`).
- **Comments**: lines starting with `#` (after prompt stripping) are dropped, NOT treated as commands.

## Placeholder detection (these cause the step to be SKIPPED, not failed)

| Pattern | Examples |
|---|---|
| Angle-bracketed | `<repo-url>`, `<your-token>`, `<path/to/file>` |
| `your-` prefix | `your-token`, `your-api-key`, `Your_Domain` |
| `YOUR_` upper | `YOUR_TOKEN`, `YOUR_API_KEY` |
| Double-bracketed | `<<PLACEHOLDER>>` |

When any of these appear, the step is flagged `skip: true` with reason `"contains placeholders requiring human input"`. The runner respects this and never attempts to execute the step.

## Classification taxonomy

| Category | Recognizes |
|---|---|
| `setup` | `git clone`, `python -m venv`, `virtualenv`, `source ... activate` |
| `install` | `pip install`, `poetry install`, `uv sync`, `pipenv install` |
| `config` | `cp .env`, edits to env files |
| `navigate` | `cd <dir>` |
| `migrate` | `migrate`, `alembic upgrade`, `db:migrate` |
| `test` | `pytest`, `unittest`, `tox`, `nose`, `mypy`, `pyright` |
| `verify` | `curl`, `wget`, `httpie`, `http` |
| `run` (long-running) | `runserver`, `uvicorn`, `gunicorn`, `flask run`, `streamlit run`, `python -m http.server`, `celery worker` |
| `other` | anything else |

The `run` category is special: those steps are executed in background and the runner polls a port to confirm readiness. Other categories are run synchronously and either pass or fail on exit code.

## Edge cases to be aware of

- **Heredocs** (`cat <<EOF ... EOF`) span multiple lines — v1 captures only the first line and treats it as a single step. This is a known under-handling.
- **Pipes and `&&`** inside one line are kept as a single command — bash handles them.
- **Interactive prompts** in a step (e.g. `npm init`, `read -p`) will hang the runner; treat any command known to be interactive as a placeholder candidate.
- **Windows-style paths** in code blocks are rare in Python project READMEs and are not specially handled.
