#!/usr/bin/env python3
"""Extract testable shell steps from a README.md file.

Reads fenced code blocks tagged bash/sh/shell/console/zsh/fish (or untagged),
splits them into individual commands, classifies each, and detects placeholders.

Usage:
    extract_steps.py <readme_path>

Output: JSON to stdout with shape:
    {
      "readme_path": str,
      "step_count": int,
      "steps": [
        {
          "index": int,
          "raw": str,
          "type": "setup" | "install" | "config" | "navigate" |
                  "migrate" | "test" | "verify" | "run" | "other",
          "placeholders": [str, ...],
          "long_running": bool,
          "language_hint": str,
          "skip": bool,
          "skip_reason": str | null
        },
        ...
      ]
    }
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

FENCE_RE = re.compile(
    r"^```(?P<lang>[a-zA-Z0-9_+-]*)\s*\n(?P<body>.*?)^```",
    re.MULTILINE | re.DOTALL,
)

SHELL_LANGS = {
    "bash", "sh", "shell", "console", "terminal",
    "zsh", "fish", "ps", "powershell", "",
}

PLACEHOLDER_PATTERNS = [
    re.compile(r"<[A-Za-z][A-Za-z0-9._/-]*>"),         # <repo-url>, <path/to/x>
    re.compile(r"\b[Yy]our[_-][A-Za-z0-9_-]+\b"),       # your-token, Your_Key
    re.compile(r"\bYOUR_[A-Z0-9_]+\b"),                  # YOUR_TOKEN
    re.compile(r"<<[A-Z][A-Z0-9_]*>>"),                  # <<PLACEHOLDER>>
]

LONG_RUNNING_HINTS = (
    "runserver", "uvicorn", "gunicorn", "flask run", "fastapi dev",
    "fastapi run", "streamlit run", "python -m http.server",
    "celery worker", "celery -A", "hypercorn", "daphne",
)

TYPE_CLASSIFIERS = (
    (re.compile(r"\bgit clone\b"), "setup"),
    (re.compile(r"\bpython -m venv\b|\bvirtualenv\b"), "setup"),
    (re.compile(r"\bsource\b.*\bactivate\b|\b\.\s+.*activate\b"), "setup"),
    (re.compile(r"\b(pip|pip3) install\b"), "install"),
    (re.compile(r"\bpoetry install\b"), "install"),
    (re.compile(r"\buv\s+(sync|pip install|venv)\b"), "install"),
    (re.compile(r"\bpipenv install\b"), "install"),
    (re.compile(r"\bcp\s+.*\.env"), "config"),
    (re.compile(r"\bcd\s+\S"), "navigate"),
    (re.compile(r"\bmigrate\b|\balembic\b"), "migrate"),
    (re.compile(r"\b(pytest|unittest|nose|tox|pyright|mypy)\b"), "test"),
    (re.compile(r"\b(curl|wget|httpie|http)\b"), "verify"),
)

PROMPT_PREFIXES = ("$ ", "# ", "> ", "PS> ", "PS C:\\")

# Characters that indicate a line is part of an ASCII directory tree or
# architecture diagram rather than a real shell command.
DECORATION_CHARS = "├│└─┌┐┘┬┴┤┼━┃┏┓┗┛→←↑↓↔↕⇒⇐⇑⇓"

# Tokens that plausibly start a shell command in a Python project README.
# Conservative list — when in doubt, omit; lines starting with an unknown verb
# get marked skip=True with reason "not_recognized_as_shell_command".
COMMAND_VERBS = {
    # shell built-ins / file ops
    "cd", "ls", "pwd", "mkdir", "rm", "rmdir", "cp", "mv", "cat", "echo",
    "touch", "chmod", "chown", "ln", "tar", "unzip", "zip",
    "source", ".", "export", "set", "unset", "alias", "exec", "eval",
    "true", "false", "test",
    # python
    "python", "python3", "py", "pip", "pip3",
    "poetry", "pipenv", "uv", "pipx", "virtualenv", "venv", "conda", "mamba",
    # web frameworks / servers
    "uvicorn", "gunicorn", "flask", "fastapi", "streamlit", "celery",
    "hypercorn", "daphne", "alembic",
    # testing / linting
    "pytest", "unittest", "tox", "mypy", "ruff", "black", "isort", "pyright",
    "flake8", "pylint", "coverage",
    # node (sometimes appears in Python repo READMEs)
    "node", "npm", "npx", "yarn", "pnpm",
    # vcs
    "git", "hg", "svn",
    # http clients
    "curl", "wget", "http", "httpie",
    # containers / orchestration
    "docker", "docker-compose", "podman", "kubectl", "helm",
    # build tools
    "make", "cmake", "ninja",
    # other lang toolchains (rare in Python READMEs but harmless)
    "cargo", "go", "ruby", "gem", "bundle", "rake",
    "java", "mvn", "gradle",
    # shells
    "bash", "sh", "zsh", "fish", "powershell", "pwsh",
    # package managers
    "sudo", "apt", "apt-get", "brew", "pacman", "yum", "dnf",
    "choco", "scoop", "winget",
    # cloud CLIs
    "aws", "gcloud", "az", "heroku",
    # databases
    "psql", "mysql", "mongosh", "sqlite3", "redis-cli",
}

_ENV_ASSIGN_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")


def looks_like_command(cmd: str) -> bool:
    """Return True iff `cmd` plausibly starts with an executable shell command.

    Filters out ASCII directory-tree decorations, architecture diagram lines,
    bare path/filename listings, and prose lines that sometimes leak into
    bash-tagged fences in READMEs.
    """
    s = cmd.strip()
    if not s:
        return False
    # ASCII tree / arrow decorations are never commands.
    if any(ch in s for ch in DECORATION_CHARS):
        return False
    first = s.split()[0]
    # Script invocation: ./foo, ./bin/foo, ~/foo, /abs/foo
    if first.startswith(("./", ".\\", "~/", "/")):
        return True
    # Env-var assignment (with or without trailing command): FOO=bar, FOO=bar cmd
    if _ENV_ASSIGN_RE.match(first):
        return True
    # Known shell command verb? Strip trailing punctuation that sometimes
    # leaks in from prose ("python:", "pip,").
    verb_orig = first.rstrip(":,;.")
    if verb_orig.lower() in COMMAND_VERBS:
        # Reject TitleCase ("Python version - 3.13") — real shell commands are
        # lowercase. Allow all-lower and all-upper (PYTHON sometimes appears).
        if verb_orig and verb_orig[0].isupper() and any(c.islower() for c in verb_orig[1:]):
            return False
        return True
    # Single bare token like "backend/", "main.py", "Windows" — definitely
    # not a command.
    if " " not in s:
        return False
    # Multi-token but unknown verb — likely prose ("Required: Gemini API Key",
    # "From the backend/ directory"). Reject.
    return False


def _strip_prompt(line: str) -> str:
    for p in PROMPT_PREFIXES:
        if line.startswith(p):
            return line[len(p):]
    return line


def split_block_into_commands(body: str) -> list[str]:
    """Split a code block body into one shell command per element.

    - Joins line continuations ending in '\\'.
    - Drops blank lines and comment-only lines.
    - Strips $ / # / > prompt prefixes used as visual decoration.
    """
    cmds: list[str] = []
    buffer: list[str] = []
    for raw_line in body.splitlines():
        line = _strip_prompt(raw_line.rstrip())
        stripped = line.lstrip()
        if not stripped:
            if buffer:
                cmds.append(" ".join(buffer))
                buffer = []
            continue
        if stripped.startswith("#") and not buffer:
            # Standalone comment, ignore
            continue
        if line.rstrip().endswith("\\"):
            buffer.append(line.rstrip()[:-1].rstrip())
        else:
            buffer.append(line)
            cmds.append(" ".join(buffer))
            buffer = []
    if buffer:
        cmds.append(" ".join(buffer))
    return [c.strip() for c in cmds if c.strip()]


def detect_placeholders(cmd: str) -> list[str]:
    found: list[str] = []
    for pat in PLACEHOLDER_PATTERNS:
        for m in pat.finditer(cmd):
            token = m.group(0)
            if token not in found:
                found.append(token)
    return found


def classify_type(cmd: str) -> str:
    for pat, label in TYPE_CLASSIFIERS:
        if pat.search(cmd):
            return label
    for hint in LONG_RUNNING_HINTS:
        if hint in cmd:
            return "run"
    return "other"


def is_long_running(cmd: str) -> bool:
    return any(hint in cmd for hint in LONG_RUNNING_HINTS)


def _find_indented_blocks(text: str, exclude_ranges: list[tuple[int, int]]) -> list[str]:
    """Find CommonMark indented code blocks (4-space / tab) outside fenced regions.

    Returns a list of block bodies with leading indentation stripped. Lines inside
    an `exclude_ranges` span are ignored (those belong to fenced blocks already
    consumed by FENCE_RE).
    """
    def in_excluded(pos: int) -> bool:
        return any(a <= pos < b for a, b in exclude_ranges)

    lines = text.splitlines(keepends=True)
    line_starts: list[int] = []
    pos = 0
    for ln in lines:
        line_starts.append(pos)
        pos += len(ln)

    blocks: list[str] = []
    i = 0
    n = len(lines)
    while i < n:
        line = lines[i]
        line_pos = line_starts[i]
        if in_excluded(line_pos):
            i += 1
            continue
        indented = line.startswith("    ") or line.startswith("\t")
        if indented and line.strip():
            # Indented blocks must be preceded by a blank line or start-of-doc
            # (otherwise they'd be paragraph continuations under CommonMark).
            prev_blank = i == 0 or not lines[i - 1].strip()
            if prev_blank:
                block_lines: list[str] = []
                j = i
                while j < n:
                    cur = lines[j]
                    if in_excluded(line_starts[j]):
                        break
                    if cur.startswith("    "):
                        block_lines.append(cur[4:])
                        j += 1
                    elif cur.startswith("\t"):
                        block_lines.append(cur[1:])
                        j += 1
                    elif not cur.strip():
                        # Blank line — block continues only if the next non-blank
                        # line is also indented.
                        k = j + 1
                        while k < n and not lines[k].strip():
                            k += 1
                        if (
                            k < n
                            and not in_excluded(line_starts[k])
                            and (lines[k].startswith("    ") or lines[k].startswith("\t"))
                        ):
                            block_lines.append("\n")
                            j += 1
                        else:
                            break
                    else:
                        break
                blocks.append("".join(block_lines))
                i = j
                continue
        i += 1
    return blocks


_UNVERIFIABLE_PREFIXES = (
    "docker ", "docker-compose ", "docker compose ", "podman ",
    "minikube ", "kind ", "kubectl ",
    "sudo ", "systemctl ", "service ",
)


def _is_unverifiable_in_container(cmd: str) -> str | None:
    """Return a skip-reason string if `cmd` can't be honestly verified inside
    the clean-room container (because it requires Docker-in-Docker, root, a
    real systemd, etc.). None otherwise.
    """
    low = cmd.strip().lower()
    if low == "docker-compose" or low == "docker compose":
        return "cannot verify in clean-room container (would require Docker-in-Docker)"
    for prefix in _UNVERIFIABLE_PREFIXES:
        if low.startswith(prefix):
            if prefix.startswith("docker"):
                return "cannot verify in clean-room container (would require Docker-in-Docker)"
            if prefix == "sudo ":
                return "cannot verify in clean-room container (requires root / privileged ops)"
            return f"cannot verify in clean-room container ({prefix.strip()} unavailable)"
    return None


def _make_step(idx: int, cmd: str, language_hint: str) -> dict:
    placeholders = detect_placeholders(cmd)
    is_cmd = looks_like_command(cmd)
    unverifiable = _is_unverifiable_in_container(cmd) if is_cmd else None
    if placeholders:
        skip = True
        skip_reason = "contains placeholders requiring human input"
    elif not is_cmd:
        skip = True
        skip_reason = "not recognized as a shell command (likely tree/diagram/prose)"
    elif unverifiable:
        skip = True
        skip_reason = unverifiable
    else:
        skip = False
        skip_reason = None
    return {
        "index": idx,
        "raw": cmd,
        "type": classify_type(cmd),
        "placeholders": placeholders,
        "long_running": is_long_running(cmd),
        "language_hint": language_hint,
        "skip": skip,
        "skip_reason": skip_reason,
    }


def extract_steps(readme_path: Path) -> dict:
    text = readme_path.read_text(encoding="utf-8")
    steps: list[dict] = []
    idx = 0
    excluded_ranges: list[tuple[int, int]] = []
    for m in FENCE_RE.finditer(text):
        excluded_ranges.append((m.start(), m.end()))
        lang = m.group("lang").lower().strip()
        if lang not in SHELL_LANGS:
            continue
        for cmd in split_block_into_commands(m.group("body")):
            steps.append(_make_step(idx, cmd, lang or "bash"))
            idx += 1
    for body in _find_indented_blocks(text, excluded_ranges):
        for cmd in split_block_into_commands(body):
            steps.append(_make_step(idx, cmd, "indented"))
            idx += 1
    return {
        "readme_path": str(readme_path),
        "step_count": len(steps),
        "steps": steps,
    }


def main() -> int:
    if len(sys.argv) != 2:
        print("Usage: extract_steps.py <readme_path>", file=sys.stderr)
        return 2
    path = Path(sys.argv[1])
    if not path.exists():
        print(f"Not found: {path}", file=sys.stderr)
        return 1
    print(json.dumps(extract_steps(path), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
