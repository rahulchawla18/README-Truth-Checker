#!/usr/bin/env python3
"""Self-tests for the parts of the pipeline that don't require Docker.

Covers:
  1. extract_steps.py  — README → ordered steps
  2. detect_python_project.py — repo → project shape
  3. diagnose.py + generate_fix.py — synthetic runtime failure → drift report → patched README

Docker-dependent parts (runner.py, open_pr.py) are NOT exercised here.
Run them manually against a real Python repo with `docker info` working.

Usage:
    python tests/run_self_tests.py
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
FIXTURES = ROOT / "tests" / "fixtures"


class TestFailure(Exception):
    pass


def _run(script: str, *args: str) -> dict:
    proc = subprocess.run(
        [sys.executable, str(SCRIPTS / script), *args],
        capture_output=True, text=True, timeout=60,
    )
    if proc.returncode != 0:
        raise TestFailure(
            f"{script} exited {proc.returncode}\nstderr:\n{proc.stderr}\nstdout:\n{proc.stdout}"
        )
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as e:
        raise TestFailure(f"{script} did not produce valid JSON: {e}\nstdout:\n{proc.stdout}")


def _assert(cond: bool, msg: str) -> None:
    if not cond:
        raise TestFailure(msg)


def test_extract_missing_env_var() -> None:
    result = _run("extract_steps.py", str(FIXTURES / "missing-env-var" / "README.md"))
    _assert(result["step_count"] == 2, f"expected 2 steps, got {result['step_count']}")
    types = [s["type"] for s in result["steps"]]
    _assert("install" in types, f"expected 'install' step, got {types}")
    cmds = [s["raw"] for s in result["steps"]]
    _assert(any("python app.py" in c for c in cmds), f"missing app.py step: {cmds}")


def test_extract_wrong_port_long_running() -> None:
    result = _run("extract_steps.py", str(FIXTURES / "wrong-port" / "README.md"))
    long_runners = [s for s in result["steps"] if s["long_running"]]
    _assert(len(long_runners) == 1, f"expected 1 long-running step, got {len(long_runners)}")
    _assert("uvicorn" in long_runners[0]["raw"], f"wrong long-runner: {long_runners[0]['raw']}")


def test_extract_placeholders_skipped() -> None:
    # Synthesize a README on the fly with placeholders
    with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False, encoding="utf-8") as f:
        f.write("# placeholders\n\n```bash\ngit clone <repo-url>\npip install -r requirements.txt\n```\n")
        path = f.name
    try:
        result = _run("extract_steps.py", path)
        skipped = [s for s in result["steps"] if s["skip"]]
        not_skipped = [s for s in result["steps"] if not s["skip"]]
        _assert(len(skipped) == 1, f"expected 1 skipped, got {len(skipped)}: {result}")
        _assert("<repo-url>" in skipped[0]["placeholders"], f"placeholder not detected: {skipped[0]}")
        _assert(len(not_skipped) == 1 and "pip install" in not_skipped[0]["raw"],
                f"non-placeholder step missing: {not_skipped}")
    finally:
        Path(path).unlink(missing_ok=True)


def test_detect_missing_env_var_fixture() -> None:
    result = _run("detect_python_project.py", str(FIXTURES / "missing-env-var"))
    _assert(result["is_python"], "missing-env-var should be detected as python")
    _assert(result["strategy"] == "pip-requirements",
            f"expected pip-requirements, got {result['strategy']}")
    _assert("app.py" in result["entry_point_hints"],
            f"app.py missing from entry hints: {result['entry_point_hints']}")


def test_detect_wrong_python_version_fixture() -> None:
    result = _run("detect_python_project.py", str(FIXTURES / "wrong-python-version"))
    _assert(result["is_python"], "wrong-python-version should be detected as python")
    _assert(result["strategy"] == "pip-pyproject",
            f"expected pip-pyproject, got {result['strategy']}")
    _assert(result["python_version"] == "3.11",
            f"expected python 3.11, got {result['python_version']}")


def test_detect_django_fixture() -> None:
    result = _run("detect_python_project.py", str(FIXTURES / "missing-migration-step"))
    _assert(result["is_python"], "django fixture should be detected as python")
    _assert(result["framework"] == "django",
            f"expected django framework, got {result['framework']}")
    _assert("manage.py" in result["entry_point_hints"],
            f"manage.py missing from entry hints: {result['entry_point_hints']}")


def test_detect_fastapi_fixture() -> None:
    result = _run("detect_python_project.py", str(FIXTURES / "wrong-port"))
    _assert(result["framework"] == "fastapi",
            f"expected fastapi framework, got {result['framework']}")


def test_diagnose_missing_env_var() -> None:
    """Feed a synthetic results.json mimicking a KeyError at runtime."""
    fixture = FIXTURES / "missing-env-var"
    synthetic = {
        "steps": [
            {
                "index": 0, "raw": "pip install -r requirements.txt",
                "type": "install", "status": "passed",
                "exit_code": 0, "stdout": "", "stderr": "",
            },
            {
                "index": 1, "raw": "python app.py",
                "type": "other", "status": "failed",
                "exit_code": 1, "stdout": "",
                "stderr": "Traceback (most recent call last):\n  File \"app.py\", line 6, in main\n    db_url = os.environ[\"DATABASE_URL\"]\nKeyError: 'DATABASE_URL'\n",
            },
        ]
    }
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as f:
        json.dump(synthetic, f)
        results_path = f.name
    try:
        report = _run("diagnose.py", str(fixture), results_path)
        _assert(report["drift_count"] == 1, f"expected 1 drift point, got {report['drift_count']}")
        point = report["drift_points"][0]
        _assert(point["cause"] == "missing_env_var",
                f"expected missing_env_var, got {point['cause']}")
        _assert(point["evidence"]["missing_var"] == "DATABASE_URL",
                f"wrong var: {point['evidence']}")
        _assert(point["evidence"]["in_env_example"] is True,
                f".env.example detection failed: {point['evidence']}")
        _assert(len(point["evidence"]["found_in_code"]) > 0,
                f"code reference not found: {point['evidence']}")
        _assert(point["confidence"] == "high",
                f"expected high confidence, got {point['confidence']}")
    finally:
        Path(results_path).unlink(missing_ok=True)


def test_generate_fix_missing_env_var() -> None:
    """End-to-end: synthetic failure → diagnose → generate_fix → patched README has the export line."""
    fixture = FIXTURES / "missing-env-var"
    synthetic = {
        "steps": [
            {
                "index": 0, "raw": "pip install -r requirements.txt",
                "type": "install", "status": "passed",
                "exit_code": 0, "stdout": "", "stderr": "",
            },
            {
                "index": 1, "raw": "python app.py",
                "type": "other", "status": "failed",
                "exit_code": 1, "stdout": "",
                "stderr": "KeyError: 'DATABASE_URL'\n",
            },
        ]
    }
    with tempfile.TemporaryDirectory() as td:
        results_path = Path(td) / "results.json"
        results_path.write_text(json.dumps(synthetic), encoding="utf-8")

        report = _run("diagnose.py", str(fixture), str(results_path))

        report_path = Path(td) / "drift.json"
        report_path.write_text(json.dumps(report), encoding="utf-8")

        fix = _run("generate_fix.py", str(fixture / "README.md"), str(report_path))
        _assert(fix["changed"], f"expected README to change, got: {fix['notes']}")
        _assert(fix["applied_fixes"] == 1, f"expected 1 fix applied, got {fix['applied_fixes']}")
        _assert("export DATABASE_URL=" in fix["patched"],
                f"export line missing from patched README:\n{fix['patched']}")
        _assert("python app.py" in fix["patched"],
                "original command should still be present")
        _assert(".env.example" in fix["patched"],
                "env example reference should be in the note")


def test_diagnose_wrong_port() -> None:
    fixture = FIXTURES / "wrong-port"
    synthetic = {
        "steps": [
            {
                "index": 0, "raw": "pip install -r requirements.txt",
                "type": "install", "status": "passed",
                "exit_code": 0, "stdout": "", "stderr": "",
            },
            {
                "index": 1, "raw": "uvicorn app:app --host 0.0.0.0 --port 3000",
                "type": "run", "status": "failed", "long_running": True,
                "port_checked": 3000, "ready": False,
                "exit_code": 1, "stdout": "", "stderr": "never became ready",
            },
        ]
    }
    with tempfile.TemporaryDirectory() as td:
        results_path = Path(td) / "results.json"
        results_path.write_text(json.dumps(synthetic), encoding="utf-8")
        report = _run("diagnose.py", str(fixture), str(results_path))

        _assert(report["drift_count"] == 1, f"expected 1 drift point, got {report['drift_count']}")
        point = report["drift_points"][0]
        _assert(point["cause"] == "long_running_not_ready",
                f"expected long_running_not_ready, got {point['cause']}")
        _assert(point["evidence"]["port_checked"] == 3000,
                f"expected port_checked=3000, got {point['evidence']}")
        _assert(point["evidence"]["detected_actual_port"] == 8000,
                f"expected to detect port 8000, got {point['evidence']}")


def test_generate_fix_wrong_port() -> None:
    fixture = FIXTURES / "wrong-port"
    drift = {
        "drift_points": [
            {
                "step_index": 1,
                "raw": "uvicorn app:app --host 0.0.0.0 --port 3000",
                "cause": "long_running_not_ready",
                "evidence": {"port_checked": 3000, "detected_actual_port": 8000},
                "suggested_diff": "...",
                "confidence": "medium",
            }
        ]
    }
    with tempfile.TemporaryDirectory() as td:
        drift_path = Path(td) / "drift.json"
        drift_path.write_text(json.dumps(drift), encoding="utf-8")
        fix = _run("generate_fix.py", str(fixture / "README.md"), str(drift_path))
        _assert(fix["changed"], "expected README to change for wrong-port")
        _assert(":8000" in fix["patched"], f"expected :8000 in patched, got:\n{fix['patched']}")
        _assert("--port 8000" in fix["patched"],
                f"expected '--port 8000' in patched, got:\n{fix['patched']}")
        _assert(":3000" not in fix["patched"] or "8000" in fix["patched"],
                "old port should be replaced")


TESTS = [
    test_extract_missing_env_var,
    test_extract_wrong_port_long_running,
    test_extract_placeholders_skipped,
    test_detect_missing_env_var_fixture,
    test_detect_wrong_python_version_fixture,
    test_detect_django_fixture,
    test_detect_fastapi_fixture,
    test_diagnose_missing_env_var,
    test_generate_fix_missing_env_var,
    test_diagnose_wrong_port,
    test_generate_fix_wrong_port,
]


def main() -> int:
    passed = 0
    failed: list[tuple[str, str]] = []
    for test in TESTS:
        try:
            test()
            print(f"  PASS  {test.__name__}")
            passed += 1
        except TestFailure as e:
            print(f"  FAIL  {test.__name__}")
            failed.append((test.__name__, str(e)))
        except Exception as e:
            print(f"  ERROR {test.__name__}: {type(e).__name__}: {e}")
            failed.append((test.__name__, f"{type(e).__name__}: {e}"))

    print()
    print(f"{passed} passed, {len(failed)} failed, out of {len(TESTS)} total.")
    if failed:
        print()
        for name, msg in failed:
            print(f"--- {name} ---")
            print(msg)
            print()
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
