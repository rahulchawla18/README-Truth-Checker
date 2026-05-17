#!/usr/bin/env python3
"""Generate a patched README from a drift report.

Usage:
    generate_fix.py <readme_path> <drift_report_json>

Output: JSON to stdout containing:
    {
      "original": str,
      "patched": str,
      "diff": str,
      "notes": [str, ...],
      "changed": bool,
      "applied_fixes": int,
      "unapplied_drift": [dict, ...]   # drift points that had no machine-applyable fix
    }
"""
from __future__ import annotations

import difflib
import json
import re
import sys
from pathlib import Path


def _line_indent(line: str) -> str:
    return line[: len(line) - len(line.lstrip())]


def insert_env_var_note(content: str, point: dict) -> tuple[str, bool]:
    var = point.get("evidence", {}).get("missing_var")
    raw = point.get("raw", "")
    if not var or not raw:
        return content, False
    export_marker = f"export {var}="
    if export_marker in content:
        return content, False  # already documented somewhere

    env_example = point.get("evidence", {}).get("env_example_file")
    note_text = (
        f"# Set {var} in your .env file (see {env_example})"
        if env_example
        else f"# Set {var} before running"
    )

    lines = content.splitlines(keepends=False)
    out: list[str] = []
    inserted = False
    for line in lines:
        if not inserted and raw.strip() in line:
            indent = _line_indent(line)
            out.append(f"{indent}{note_text}")
            out.append(f"{indent}export {var}=<your-value>")
            inserted = True
        out.append(line)
    new_content = "\n".join(out)
    if content.endswith("\n"):
        new_content += "\n"
    return new_content, inserted


def fix_install_command(content: str, point: dict) -> tuple[str, bool]:
    suggested = point.get("suggested_diff") or ""
    if "pip install ." in suggested and "pip install -r requirements.txt" in content:
        return content.replace("pip install -r requirements.txt", "pip install ."), True
    if "poetry install" in suggested and "pip install" in content:
        return content.replace("pip install -r requirements.txt", "poetry install"), True
    if "uv sync" in suggested and "pip install" in content:
        return content.replace("pip install -r requirements.txt", "uv sync"), True
    return content, False


def fix_wrong_port(content: str, point: dict) -> tuple[str, bool]:
    ev = point.get("evidence", {})
    old = ev.get("port_checked")
    new = ev.get("detected_actual_port")
    if not old or not new or old == new:
        return content, False
    patched = content
    patched = re.sub(rf":{old}\b", f":{new}", patched)
    patched = re.sub(rf"--port[=\s]+{old}\b", f"--port {new}", patched)
    return patched, patched != content


def fix_moved_command(content: str, point: dict) -> tuple[str, bool]:
    ev = point.get("evidence", {})

    # Path A: portability rewrite (py -> python3, docker-compose -> docker compose).
    rewrite_from = ev.get("rewrite_from")
    rewrite_to = ev.get("rewrite_to")
    if rewrite_from and rewrite_to:
        # Match the token only at command position: start-of-line (with optional
        # leading whitespace) or after common shell separators. Avoid replacing
        # the token inside prose like "Use `py` for...".
        pattern = re.compile(
            rf"(^|[\s;&|`(])({re.escape(rewrite_from)})(?=[\s\n]|$)",
            re.MULTILINE,
        )
        new = pattern.sub(lambda m: f"{m.group(1)}{rewrite_to}", content)
        if new != content:
            return new, True
        return content, False

    # Path B: moved Python entry-point file.
    cmd = ev.get("command") or ""
    moved = ev.get("found_at")
    if not cmd or not moved:
        return content, False
    # Replace `python manage.py` with `python path/to/manage.py` (only when bare)
    pattern = rf"\bpython\s+{re.escape(cmd)}\b"
    replacement = f"python {moved}"
    new = re.sub(pattern, replacement, content)
    if new != content:
        return new, True
    # Plain `manage.py runserver` -> `python <moved> runserver`
    pattern2 = rf"\b{re.escape(cmd)}\b"
    new2 = re.sub(pattern2, moved, content)
    return new2, new2 != content


FIX_HANDLERS = {
    "missing_env_var": insert_env_var_note,
    "missing_module_after_install": fix_install_command,
    "long_running_not_ready": fix_wrong_port,
    "command_not_found": fix_moved_command,
}


def generate_fix(readme: Path, report: dict) -> dict:
    original = readme.read_text(encoding="utf-8")
    patched = original
    notes: list[str] = []
    applied = 0
    unapplied: list[dict] = []

    for point in report.get("drift_points", []):
        handler = FIX_HANDLERS.get(point.get("cause"))
        if not handler:
            unapplied.append(point)
            continue
        new_content, changed = handler(patched, point)
        if changed:
            patched = new_content
            applied += 1
            notes.append(
                f"[{point.get('cause')}] applied fix for step "
                f"{point.get('step_index')}: {point.get('raw', '')[:80]}"
            )
        else:
            unapplied.append(point)

    diff = "\n".join(difflib.unified_diff(
        original.splitlines(),
        patched.splitlines(),
        fromfile="README.md (current)",
        tofile="README.md (proposed)",
        lineterm="",
    ))

    return {
        "original": original,
        "patched": patched,
        "diff": diff,
        "notes": notes,
        "changed": original != patched,
        "applied_fixes": applied,
        "unapplied_drift": unapplied,
    }


def main() -> int:
    if len(sys.argv) != 3:
        print("Usage: generate_fix.py <readme_path> <drift_report_json>", file=sys.stderr)
        return 2
    readme = Path(sys.argv[1])
    drift = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
    print(json.dumps(generate_fix(readme, drift), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
