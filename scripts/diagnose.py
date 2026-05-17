#!/usr/bin/env python3
"""Diagnose README step failures by inspecting the real codebase.

Usage:
    diagnose.py <repo_path> <results_json>

Output: drift_report JSON to stdout with shape:
    {
      "drift_count": int,
      "drift_points": [
        {
          "step_index": int,
          "raw": str,
          "cause": str,
          "evidence": {...},
          "suggested_diff": str | null,
          "confidence": "high" | "medium" | "low"
        },
        ...
      ]
    }
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

ENV_VAR_PATTERNS = (
    re.compile(r"""os\.environ\[\s*['"]([A-Z][A-Z0-9_]*)['"]\s*\]"""),
    re.compile(r"""os\.environ\.get\(\s*['"]([A-Z][A-Z0-9_]*)['"]"""),
    re.compile(r"""os\.getenv\(\s*['"]([A-Z][A-Z0-9_]*)['"]"""),
    re.compile(r"""\bgetenv\(\s*['"]([A-Z][A-Z0-9_]*)['"]"""),
)
KEYERROR_RE = re.compile(r"KeyError:\s*['\"]([A-Z_][A-Z0-9_]*)['\"]")
ENV_NOT_SET_RE = re.compile(
    r"(?:environment variable )?['\"]?([A-Z][A-Z0-9_]+)['\"]?\s+(?:is not set|not set|must be set|required)",
    re.IGNORECASE,
)
MODULE_NOT_FOUND_RE = re.compile(r"ModuleNotFoundError:\s*No module named\s+['\"]([^'\"]+)['\"]")
NOT_FOUND_RE = re.compile(r"(?:command not found|not found|No such file)", re.IGNORECASE)
PORT_IN_USE_RE = re.compile(r"(?:Address already in use|port \d+ is already)", re.IGNORECASE)

SKIP_DIRS = {"venv", ".venv", "node_modules", ".git", "__pycache__",
             "build", "dist", ".tox", ".pytest_cache", ".mypy_cache"}


def _skipped(path: Path) -> bool:
    return any(part in SKIP_DIRS for part in path.parts)


def _truncate(s: str | None, n: int = 500) -> str:
    if not s:
        return ""
    return s if len(s) <= n else s[:n] + " ..."


def diagnose_failure(repo: Path, step: dict) -> dict:
    cmd = step.get("raw", "")
    combined = (step.get("stderr") or "") + "\n" + (step.get("stdout") or "")

    m = KEYERROR_RE.search(combined)
    if m:
        return diagnose_missing_env_var(repo, step, m.group(1))

    m = ENV_NOT_SET_RE.search(combined)
    if m and m.group(1).isupper():
        return diagnose_missing_env_var(repo, step, m.group(1))

    m = MODULE_NOT_FOUND_RE.search(combined)
    if m:
        return diagnose_missing_module(repo, step, m.group(1))

    if step.get("long_running") and step.get("status") == "failed":
        return diagnose_long_running_failure(repo, step)

    if PORT_IN_USE_RE.search(combined):
        return diagnose_long_running_failure(repo, step)

    if NOT_FOUND_RE.search(combined):
        return diagnose_command_not_found(repo, step)

    return {
        "step_index": step.get("index"),
        "raw": cmd,
        "cause": "unknown",
        "evidence": {"stderr_excerpt": _truncate(combined, 600)},
        "suggested_diff": None,
        "confidence": "low",
    }


def diagnose_missing_env_var(repo: Path, step: dict, var: str) -> dict:
    evidence: dict = {"missing_var": var, "found_in_code": [], "in_env_example": False}

    for py in repo.rglob("*.py"):
        if _skipped(py):
            continue
        try:
            text = py.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        for pat in ENV_VAR_PATTERNS:
            for m in pat.finditer(text):
                if m.group(1) == var:
                    line_no = text[:m.start()].count("\n") + 1
                    rel = py.relative_to(repo).as_posix()
                    evidence["found_in_code"].append(f"{rel}:{line_no}")
        if len(evidence["found_in_code"]) >= 8:
            break

    for env_name in (".env.example", ".env.sample", ".env.template", "example.env"):
        p = repo / env_name
        if p.exists():
            try:
                t = p.read_text(encoding="utf-8")
                if re.search(rf"^{re.escape(var)}\s*=", t, re.MULTILINE):
                    evidence["in_env_example"] = True
                    evidence["env_example_file"] = env_name
            except Exception:
                pass
            break

    if evidence["found_in_code"]:
        first = evidence["found_in_code"][0].split(":")[0]
        try:
            log = subprocess.run(
                ["git", "log", "-S", var, "--pretty=format:%h %an %ad %s",
                 "--date=short", "-n", "1", "--", first],
                cwd=repo, capture_output=True, text=True, timeout=10,
            )
            if log.returncode == 0 and log.stdout.strip():
                evidence["git_added_in"] = log.stdout.strip()
        except Exception:
            pass

    note = (
        f"# Set {var} in your .env file (see {evidence.get('env_example_file', '.env.example')})"
        if evidence.get("in_env_example")
        else f"# Set {var} before running"
    )
    suggested = (
        f"Before the failing command `{step.get('raw')}`, add:\n"
        f"    {note}\n"
        f"    export {var}=<your-value>"
    )

    return {
        "step_index": step.get("index"),
        "raw": step.get("raw"),
        "cause": "missing_env_var",
        "evidence": evidence,
        "suggested_diff": suggested,
        "confidence": "high" if evidence["found_in_code"] else "medium",
    }


def diagnose_missing_module(repo: Path, step: dict, module: str) -> dict:
    found = [
        name for name in ("pyproject.toml", "requirements.txt", "Pipfile",
                          "poetry.lock", "uv.lock", "setup.py")
        if (repo / name).exists()
    ]

    suggested: str | None = None
    raw = step.get("raw", "")
    if "pyproject.toml" in found and "requirements.txt" not in found and "pip install -r requirements.txt" in raw:
        suggested = (
            "README runs `pip install -r requirements.txt` but the project is "
            "configured via pyproject.toml. Replace with `pip install .` (or "
            "`poetry install` / `uv sync` if using those tools)."
        )
    elif "poetry.lock" in found and "poetry install" not in raw and "pip install" in raw:
        suggested = (
            "Project uses Poetry (poetry.lock present). Replace pip install with `poetry install`."
        )
    elif "uv.lock" in found and "uv sync" not in raw and "pip install" in raw:
        suggested = (
            "Project uses uv (uv.lock present). Replace pip install with `uv sync`."
        )

    return {
        "step_index": step.get("index"),
        "raw": step.get("raw"),
        "cause": "missing_module_after_install",
        "evidence": {"module": module, "project_files_found": found},
        "suggested_diff": suggested,
        "confidence": "medium" if suggested else "low",
    }


PORT_LITERAL_RE = re.compile(r"(?:PORT|port)\s*[:=]\s*['\"]?(\d{2,5})")


def _detect_project_default_port(repo: Path, cmd: str) -> int | None:
    for fname in ("manage.py", "asgi.py", "wsgi.py", "main.py", "app.py",
                  "config.py", "settings.py", "server.py"):
        p = repo / fname
        if not p.exists():
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        m = PORT_LITERAL_RE.search(text)
        if m:
            try:
                return int(m.group(1))
            except ValueError:
                continue
    if "runserver" in cmd:
        return 8000
    if "uvicorn" in cmd or "fastapi" in cmd:
        return 8000
    if "flask" in cmd:
        return 5000
    if "streamlit" in cmd:
        return 8501
    return None


def diagnose_long_running_failure(repo: Path, step: dict) -> dict:
    raw = step.get("raw", "")
    expected = step.get("port_checked")
    actual = _detect_project_default_port(repo, raw)

    suggested: str | None = None
    if expected and actual and expected != actual:
        suggested = (
            f"README appears to use port {expected}, but the project starts on port {actual}. "
            f"Update the URLs and any `--port` flags in the README to {actual}."
        )
    elif not expected and actual:
        suggested = (
            f"Server start did not become ready on a guessable port. The project default "
            f"appears to be {actual}. Verify the README's URLs reference port {actual}."
        )

    return {
        "step_index": step.get("index"),
        "raw": raw,
        "cause": "long_running_not_ready",
        "evidence": {
            "port_checked": expected,
            "detected_actual_port": actual,
            "stderr_excerpt": _truncate(step.get("stderr"), 400),
        },
        "suggested_diff": suggested,
        "confidence": "medium" if (expected and actual) else "low",
    }


# Known portability rewrites: command-not-found on the LEFT, recommended
# cross-platform replacement on the RIGHT. Used when the failing command's
# first token matches a key here.
PORTABILITY_REWRITES: dict[str, tuple[str, str]] = {
    # Windows-only Python launcher → portable invocation
    "py": ("python3", "`py` is the Windows-only Python launcher; use `python3` for Linux/Mac portability."),
    # Docker Compose v1 standalone binary → v2 plugin syntax
    "docker-compose": ("docker compose", "`docker-compose` (v1) is deprecated; modern Docker installs ship `docker compose` (v2, space-separated)."),
}


def diagnose_command_not_found(repo: Path, step: dict) -> dict:
    raw = step.get("raw", "")
    first_word = raw.split()[0] if raw.split() else ""

    # 1. Known portability rewrites (py, docker-compose, ...) — high confidence,
    #    machine-applyable.
    if first_word in PORTABILITY_REWRITES:
        replacement, rationale = PORTABILITY_REWRITES[first_word]
        return {
            "step_index": step.get("index"),
            "raw": raw,
            "cause": "command_not_found",
            "evidence": {
                "command": first_word,
                "found_at": None,
                "rewrite_from": first_word,
                "rewrite_to": replacement,
                "rationale": rationale,
            },
            "suggested_diff": f"Replace `{first_word}` with `{replacement}`. {rationale}",
            "confidence": "high",
        }

    # 2. Moved Python entry-point: same filename exists deeper in the tree.
    moved_path: str | None = None
    if first_word.endswith(".py") or first_word in {"manage.py", "main.py", "app.py"}:
        candidates = list(repo.rglob(first_word))
        for c in candidates:
            if not _skipped(c) and c.parent != repo:
                moved_path = c.relative_to(repo).as_posix()
                break

    suggested: str | None = None
    if moved_path:
        suggested = (
            f"`{first_word}` was not found in /workspace, but was found at `{moved_path}`. "
            f"Update the README to run `python {moved_path}` (or `cd` into its directory first)."
        )

    return {
        "step_index": step.get("index"),
        "raw": raw,
        "cause": "command_not_found",
        "evidence": {"command": first_word, "found_at": moved_path},
        "suggested_diff": suggested,
        "confidence": "high" if moved_path else "low",
    }


def main() -> int:
    if len(sys.argv) != 3:
        print("Usage: diagnose.py <repo_path> <results_json>", file=sys.stderr)
        return 2

    repo = Path(sys.argv[1]).resolve()
    results = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))

    drift: list[dict] = []
    for step in results.get("steps", []):
        if step.get("status") == "failed":
            drift.append(diagnose_failure(repo, step))

    print(json.dumps({
        "drift_count": len(drift),
        "drift_points": drift,
    }, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
