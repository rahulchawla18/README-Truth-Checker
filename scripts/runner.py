#!/usr/bin/env python3
"""Execute README steps in an isolated Docker container.

Strategy:
  1. Generate a Dockerfile based on the detected Python project shape.
  2. Build a cached image (tag = content hash).
  3. Start ONE long-lived container with the repo mounted at /workspace.
  4. Run each step via `docker exec`, persisting cwd via an echo trick.
  5. Long-running steps (servers) start detached, poll their port, then get killed.
  6. Stop on first failure — don't pretend later steps would have worked.

Usage:
    runner.py <repo_path> <project_json> <steps_json>

Output: JSON report to stdout.
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

DEFAULT_STEP_TIMEOUT = 180
LONG_RUNNING_READY_TIMEOUT = 25  # seconds to wait for a server to become ready

DOCKERFILE_TEMPLATE = """\
FROM python:{python_version}-slim-bookworm

ENV DEBIAN_FRONTEND=noninteractive \\
    PIP_DISABLE_PIP_VERSION_CHECK=1 \\
    PIP_NO_INPUT=1

RUN apt-get update && apt-get install -y --no-install-recommends \\
    git curl ca-certificates build-essential \\
    && rm -rf /var/lib/apt/lists/*

{package_manager_install}

WORKDIR /workspace
"""

PACKAGE_MANAGER_INSTALLERS = {
    "poetry": "RUN pip install --no-cache-dir poetry==1.8.3",
    "uv": "RUN pip install --no-cache-dir uv",
    "pipenv": "RUN pip install --no-cache-dir pipenv",
    "pip-requirements": "",
    "pip-pyproject": "",
    "setup-py": "",
}

EXPLICIT_PORT_PATTERNS = (
    re.compile(r"--port[=\s]+(\d+)"),
    re.compile(r"\s-p\s+(\d+)"),
    re.compile(r"\b(?:host|0\.0\.0\.0|127\.0\.0\.1|localhost):(\d{2,5})\b"),
    re.compile(r"\brunserver\s+(?:[^\s]+:)?(\d{2,5})"),
)


def _truncate(s: str, max_len: int = 4000) -> str:
    if s is None:
        return ""
    if len(s) <= max_len:
        return s
    return s[:max_len] + f"\n... (truncated; {len(s) - max_len} more chars)"


def generate_dockerfile(project: dict) -> str:
    py = project.get("python_version") or "3.12"
    if not re.match(r"^\d+\.\d+$", py):
        py = "3.12"
    strategy = project.get("strategy") or "pip-requirements"
    return DOCKERFILE_TEMPLATE.format(
        python_version=py,
        package_manager_install=PACKAGE_MANAGER_INSTALLERS.get(strategy, ""),
    )


def _hash_inputs(dockerfile: str, repo: Path) -> str:
    h = hashlib.sha256()
    h.update(dockerfile.encode())
    for fname in (
        "pyproject.toml", "requirements.txt", "Pipfile",
        "Pipfile.lock", "poetry.lock", "uv.lock", "setup.py", "setup.cfg",
    ):
        p = repo / fname
        if p.exists():
            try:
                h.update(p.read_bytes())
            except Exception:
                pass
    return h.hexdigest()[:12]


def build_image(repo: Path, dockerfile: str) -> tuple[str, str]:
    tag = f"readme-truth-checker:{_hash_inputs(dockerfile, repo)}"
    inspect = subprocess.run(
        ["docker", "image", "inspect", tag],
        capture_output=True, text=True,
    )
    if inspect.returncode == 0:
        return tag, "(image cached, skipped build)"

    with tempfile.NamedTemporaryFile("w", suffix=".Dockerfile", delete=False) as f:
        f.write(dockerfile)
        df_path = f.name
    try:
        result = subprocess.run(
            ["docker", "build", "-t", tag, "-f", df_path, str(repo)],
            capture_output=True, text=True, timeout=900,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"docker build failed (exit {result.returncode}):\n"
                f"{_truncate(result.stderr or result.stdout, 2000)}"
            )
        return tag, _truncate(result.stdout + result.stderr, 1500)
    finally:
        Path(df_path).unlink(missing_ok=True)


def detect_port(cmd: str, framework_hint: str | None = None) -> int | None:
    for pat in EXPLICIT_PORT_PATTERNS:
        m = pat.search(cmd)
        if m:
            try:
                return int(m.group(1))
            except ValueError:
                continue
    if "runserver" in cmd:
        return 8000
    if "uvicorn" in cmd or "fastapi" in cmd or "hypercorn" in cmd:
        return 8000
    if "flask run" in cmd or "flask " in cmd:
        return 5000
    if "streamlit run" in cmd:
        return 8501
    if framework_hint == "django":
        return 8000
    if framework_hint == "fastapi":
        return 8000
    if framework_hint == "flask":
        return 5000
    return None


def start_container(image: str, repo: Path) -> str:
    """Start a long-lived container with the repo mounted at /workspace."""
    name = f"rtc-run-{int(time.time())}-{hashlib.sha1(str(repo).encode()).hexdigest()[:6]}"
    cmd = [
        "docker", "run", "-d", "--rm",
        "--name", name,
        # Mount as read-only? No — README steps will write (pip install, .env edits).
        "-v", f"{str(repo)}:/workspace",
        # Publish a broad port range so long-running step probes can reach the container.
        # We can't know the port up front, so we use host networking when on Linux;
        # on Windows/Mac Docker Desktop, host networking is unsupported, so we fall back
        # to publishing a wide range. For simplicity, we exec curl from INSIDE the container.
        image,
        "bash", "-lc", "tail -f /dev/null",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    if result.returncode != 0:
        raise RuntimeError(f"failed to start container: {result.stderr}")
    return name


def stop_container(name: str) -> None:
    subprocess.run(["docker", "kill", name], capture_output=True, text=True)


def run_normal_step(container: str, cwd: str, cmd: str) -> tuple[dict, str]:
    """Run a regular step. Returns (result_dict, new_cwd)."""
    wrapped = (
        f"set -e; cd {_shell_quote(cwd)} 2>/dev/null || cd /workspace; "
        f"{cmd}; __rc=$?; echo \"__CWD__:$(pwd)\"; exit $__rc"
    )
    docker_cmd = ["docker", "exec", container, "bash", "-lc", wrapped]
    start = time.time()
    try:
        proc = subprocess.run(
            docker_cmd, capture_output=True, text=True, timeout=DEFAULT_STEP_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        return {
            "exit_code": -1,
            "stdout": "",
            "stderr": f"timed out after {DEFAULT_STEP_TIMEOUT}s",
            "duration_sec": float(DEFAULT_STEP_TIMEOUT),
            "timed_out": True,
        }, cwd

    duration = round(time.time() - start, 2)
    stdout = proc.stdout or ""
    new_cwd = cwd
    m = re.search(r"^__CWD__:(.+)$", stdout, re.MULTILINE)
    if m:
        new_cwd = m.group(1).strip()
        stdout = re.sub(r"\n?__CWD__:.+$", "", stdout, flags=re.MULTILINE)
    return {
        "exit_code": proc.returncode,
        "stdout": _truncate(stdout),
        "stderr": _truncate(proc.stderr or ""),
        "duration_sec": duration,
        "timed_out": False,
    }, new_cwd


def run_long_running_step(
    container: str, cwd: str, cmd: str, framework_hint: str | None
) -> dict:
    """Start a server step in background, poll its port, then kill it."""
    port = detect_port(cmd, framework_hint)
    bg_cmd = (
        f"cd {_shell_quote(cwd)} 2>/dev/null || cd /workspace; "
        f"nohup bash -c {_shell_quote(cmd)} > /tmp/longrun.log 2>&1 & echo $!"
    )
    spawn = subprocess.run(
        ["docker", "exec", container, "bash", "-lc", bg_cmd],
        capture_output=True, text=True, timeout=15,
    )
    if spawn.returncode != 0:
        return {
            "exit_code": -1,
            "stdout": "",
            "stderr": _truncate(spawn.stderr or "failed to spawn"),
            "duration_sec": 0.0,
            "timed_out": False,
            "long_running": True,
            "port_checked": port,
            "ready": False,
        }
    pid = (spawn.stdout or "").strip().splitlines()[-1] if spawn.stdout else ""

    ready = False
    start = time.time()
    if port:
        for _ in range(LONG_RUNNING_READY_TIMEOUT):
            time.sleep(1)
            alive = subprocess.run(
                ["docker", "exec", container, "bash", "-lc", f"kill -0 {pid} 2>/dev/null && echo up || echo down"],
                capture_output=True, text=True, timeout=5,
            )
            if "up" not in (alive.stdout or ""):
                break
            # Probe for "server is listening and speaking HTTP" — any HTTP
            # status code counts as ready, including 404 (common when the app
            # is an API with no root route). We deliberately do NOT use
            # `curl -fsS` which would treat 4xx/5xx as failure.
            probe = subprocess.run(
                ["docker", "exec", container, "bash", "-lc",
                 f"code=$(curl -s -o /dev/null -m 2 -w '%{{http_code}}' http://127.0.0.1:{port}/ 2>/dev/null); "
                 f"[ -n \"$code\" ] && [ \"$code\" != \"000\" ] && exit 0 || exit 1"],
                capture_output=True, text=True, timeout=5,
            )
            if probe.returncode == 0:
                ready = True
                break
    else:
        time.sleep(5)
        alive = subprocess.run(
            ["docker", "exec", container, "bash", "-lc", f"kill -0 {pid} 2>/dev/null && echo up || echo down"],
            capture_output=True, text=True, timeout=5,
        )
        ready = "up" in (alive.stdout or "")

    logs = subprocess.run(
        ["docker", "exec", container, "bash", "-lc", "cat /tmp/longrun.log 2>/dev/null || true"],
        capture_output=True, text=True, timeout=10,
    )
    subprocess.run(
        ["docker", "exec", container, "bash", "-lc", f"kill {pid} 2>/dev/null || true"],
        capture_output=True, text=True, timeout=5,
    )

    return {
        "exit_code": 0 if ready else 1,
        "stdout": _truncate(logs.stdout or ""),
        "stderr": "" if ready else "long-running step never became ready on detected port",
        "duration_sec": round(time.time() - start, 2),
        "timed_out": False,
        "long_running": True,
        "port_checked": port,
        "ready": ready,
    }


def _shell_quote(s: str) -> str:
    return "'" + s.replace("'", "'\\''") + "'"


def main() -> int:
    if len(sys.argv) != 4:
        print("Usage: runner.py <repo_path> <project_json> <steps_json>", file=sys.stderr)
        return 2

    if not shutil.which("docker"):
        print(json.dumps({
            "error": "docker not found in PATH",
            "image": None, "build_log_excerpt": "", "steps": [],
        }, indent=2))
        return 1

    info = subprocess.run(["docker", "info"], capture_output=True, text=True, timeout=15)
    if info.returncode != 0:
        print(json.dumps({
            "error": "docker daemon not reachable. Is Docker Desktop / dockerd running?",
            "details": _truncate(info.stderr or info.stdout, 600),
            "image": None, "build_log_excerpt": "", "steps": [],
        }, indent=2))
        return 1

    repo = Path(sys.argv[1]).resolve()
    project = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
    steps_doc = json.loads(Path(sys.argv[3]).read_text(encoding="utf-8"))
    steps = steps_doc["steps"]
    framework = project.get("framework")

    dockerfile = generate_dockerfile(project)
    try:
        image, build_log = build_image(repo, dockerfile)
    except Exception as e:
        print(json.dumps({
            "error": str(e),
            "image": None, "build_log_excerpt": "", "steps": [],
        }, indent=2))
        return 1

    container = start_container(image, repo)
    results: list[dict] = []
    cwd = "/workspace"
    failed_idx: int | None = None

    try:
        for step in steps:
            if step.get("skip"):
                results.append({
                    "index": step["index"],
                    "raw": step["raw"],
                    "type": step.get("type"),
                    "status": "skipped",
                    "reason": step.get("skip_reason"),
                })
                continue

            if step.get("long_running"):
                outcome = run_long_running_step(container, cwd, step["raw"], framework)
            else:
                outcome, cwd = run_normal_step(container, cwd, step["raw"])

            status = "passed" if outcome["exit_code"] == 0 else "failed"
            results.append({
                "index": step["index"],
                "raw": step["raw"],
                "type": step.get("type"),
                "status": status,
                **outcome,
            })
            if status == "failed":
                failed_idx = step["index"]
                break

        if failed_idx is not None:
            for s in steps:
                if s["index"] > failed_idx:
                    if s.get("skip"):
                        results.append({
                            "index": s["index"],
                            "raw": s["raw"],
                            "type": s.get("type"),
                            "status": "skipped",
                            "reason": s.get("skip_reason"),
                        })
                    else:
                        results.append({
                            "index": s["index"],
                            "raw": s["raw"],
                            "type": s.get("type"),
                            "status": "not_run",
                            "reason": f"earlier step (index {failed_idx}) failed",
                        })
    finally:
        stop_container(container)

    summary = {
        "passed": sum(1 for r in results if r.get("status") == "passed"),
        "failed": sum(1 for r in results if r.get("status") == "failed"),
        "skipped": sum(1 for r in results if r.get("status") == "skipped"),
        "not_run": sum(1 for r in results if r.get("status") == "not_run"),
        "total": len(results),
    }

    print(json.dumps({
        "image": image,
        "dockerfile": dockerfile,
        "build_log_excerpt": build_log,
        "summary": summary,
        "steps": results,
    }, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
