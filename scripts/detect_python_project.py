#!/usr/bin/env python3
"""Detect the shape of a Python project from its config files.

Usage:
    detect_python_project.py <repo_path>

Output: JSON to stdout with shape:
    {
      "is_python": bool,
      "strategy": "poetry" | "uv" | "pip-pyproject" | "pipenv" |
                  "pip-requirements" | "setup-py" | null,
      "python_version": str,            # e.g. "3.12"
      "framework": "django" | "fastapi" | "flask" | "streamlit" | null,
      "install_command": str | null,
      "entry_point_hints": [str, ...],
      "config_files_found": [str, ...]
    }
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

try:
    import tomllib  # Python 3.11+
except ModuleNotFoundError:
    tomllib = None  # type: ignore


FRAMEWORK_KEYWORDS = (
    ("django", "django"),
    ("fastapi", "fastapi"),
    ("flask", "flask"),
    ("streamlit", "streamlit"),
)

ENTRY_POINT_CANDIDATES = (
    "manage.py", "main.py", "app.py", "wsgi.py", "asgi.py", "run.py", "server.py",
)


def _normalize_python_version(spec: str) -> str:
    """Pick a concrete X.Y from a version spec like '>=3.10' or '^3.11'."""
    m = re.search(r"(\d+)\.(\d+)", spec)
    if m:
        return f"{m.group(1)}.{m.group(2)}"
    return spec.strip()


def _detect_framework(text: str) -> str | None:
    text = text.lower()
    for keyword, name in FRAMEWORK_KEYWORDS:
        if keyword in text:
            return name
    return None


def _collect_deps_text(pyproject_data: dict) -> str:
    parts: list[str] = []
    project = pyproject_data.get("project", {}) or {}
    deps = project.get("dependencies") or []
    parts.extend(str(d) for d in deps)
    opt_deps = project.get("optional-dependencies") or {}
    if isinstance(opt_deps, dict):
        for group in opt_deps.values():
            parts.extend(str(d) for d in group)
    poetry_deps = (
        pyproject_data.get("tool", {}).get("poetry", {}).get("dependencies", {})
    )
    if isinstance(poetry_deps, dict):
        parts.extend(poetry_deps.keys())
    return " ".join(parts).lower()


def detect(repo: Path) -> dict:
    result: dict = {
        "is_python": False,
        "strategy": None,
        "python_version": None,
        "framework": None,
        "install_command": None,
        "entry_point_hints": [],
        "config_files_found": [],
    }

    paths = {
        "pyproject.toml": repo / "pyproject.toml",
        "requirements.txt": repo / "requirements.txt",
        "Pipfile": repo / "Pipfile",
        "Pipfile.lock": repo / "Pipfile.lock",
        "setup.py": repo / "setup.py",
        "setup.cfg": repo / "setup.cfg",
        ".python-version": repo / ".python-version",
        "uv.lock": repo / "uv.lock",
        "poetry.lock": repo / "poetry.lock",
    }
    for name, p in paths.items():
        if p.exists():
            result["config_files_found"].append(name)
            result["is_python"] = True

    if not result["is_python"]:
        if list(repo.glob("*.py")) or (repo / "src").is_dir():
            result["is_python"] = True

    pyproject = paths["pyproject.toml"]
    pyproject_data: dict = {}
    if pyproject.exists() and tomllib is not None:
        try:
            with pyproject.open("rb") as f:
                pyproject_data = tomllib.load(f)
        except Exception as e:
            result["pyproject_parse_error"] = str(e)

    if pyproject.exists():
        tool = pyproject_data.get("tool", {}) or {}
        if "poetry" in tool:
            result["strategy"] = "poetry"
            result["install_command"] = "poetry install"
            py_dep = tool.get("poetry", {}).get("dependencies", {}).get("python")
            if py_dep:
                result["python_version"] = _normalize_python_version(str(py_dep))
        elif "uv" in tool or paths["uv.lock"].exists():
            result["strategy"] = "uv"
            result["install_command"] = "uv sync"
        else:
            result["strategy"] = "pip-pyproject"
            result["install_command"] = "pip install ."

        if not result["python_version"]:
            req = (pyproject_data.get("project", {}) or {}).get("requires-python")
            if req:
                result["python_version"] = _normalize_python_version(str(req))

        result["framework"] = _detect_framework(_collect_deps_text(pyproject_data))
    elif paths["Pipfile"].exists():
        result["strategy"] = "pipenv"
        result["install_command"] = "pipenv install --dev"
    elif paths["requirements.txt"].exists():
        result["strategy"] = "pip-requirements"
        result["install_command"] = "pip install -r requirements.txt"
        try:
            text = paths["requirements.txt"].read_text(encoding="utf-8")
            result["framework"] = _detect_framework(text)
        except Exception:
            pass
    elif paths["setup.py"].exists():
        result["strategy"] = "setup-py"
        result["install_command"] = "pip install ."

    pv_file = paths[".python-version"]
    if pv_file.exists():
        try:
            tokens = pv_file.read_text(encoding="utf-8").strip().split()
            if tokens:
                result["python_version"] = _normalize_python_version(tokens[0])
        except Exception:
            pass

    if not result["python_version"]:
        result["python_version"] = "3.12"

    for candidate in ENTRY_POINT_CANDIDATES:
        if (repo / candidate).exists():
            result["entry_point_hints"].append(candidate)

    return result


def main() -> int:
    if len(sys.argv) != 2:
        print("Usage: detect_python_project.py <repo_path>", file=sys.stderr)
        return 2
    repo = Path(sys.argv[1])
    if not repo.is_dir():
        print(f"Not a directory: {repo}", file=sys.stderr)
        return 1
    print(json.dumps(detect(repo), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
