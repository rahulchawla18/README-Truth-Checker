#!/usr/bin/env python3
"""Open a PR with the corrected README via gh CLI.

Creates a new branch off the current HEAD, writes the patched README, commits,
pushes, and runs `gh pr create`. Exits non-zero with a clear error if any step
fails.

Usage:
    open_pr.py <repo_path> <patched_readme_path> <pr_body_path>
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import time
from pathlib import Path


def _run(cmd: list[str], cwd: Path, *, timeout: int = 60) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)


def main() -> int:
    if len(sys.argv) != 4:
        print("Usage: open_pr.py <repo_path> <patched_readme_path> <pr_body_path>",
              file=sys.stderr)
        return 2

    repo = Path(sys.argv[1]).resolve()
    patched = Path(sys.argv[2])
    pr_body = Path(sys.argv[3])

    if not repo.is_dir():
        print(f"ERROR: repo path is not a directory: {repo}", file=sys.stderr)
        return 1
    if not (repo / ".git").exists():
        print(f"ERROR: {repo} is not a git repo (no .git directory).", file=sys.stderr)
        return 1
    if not patched.is_file():
        print(f"ERROR: patched README not found: {patched}", file=sys.stderr)
        return 1
    if not pr_body.is_file():
        print(f"ERROR: PR body file not found: {pr_body}", file=sys.stderr)
        return 1

    if not shutil.which("gh"):
        print("ERROR: gh CLI not found in PATH. Install from https://cli.github.com/",
              file=sys.stderr)
        return 2

    auth = _run(["gh", "auth", "status"], repo)
    if auth.returncode != 0:
        print("ERROR: gh CLI is not authenticated. Run `gh auth login` first.",
              file=sys.stderr)
        print(auth.stderr, file=sys.stderr)
        return 3

    # Refuse to clobber uncommitted changes in README.md
    status = _run(["git", "status", "--porcelain", "README.md"], repo)
    if status.stdout.strip():
        print("ERROR: README.md has uncommitted changes. Commit or stash before running.",
              file=sys.stderr)
        return 4

    branch = f"readme-truth-check/{time.strftime('%Y%m%d-%H%M%S')}"
    co = _run(["git", "checkout", "-b", branch], repo)
    if co.returncode != 0:
        print(f"ERROR creating branch: {co.stderr}", file=sys.stderr)
        return 5

    try:
        (repo / "README.md").write_text(
            patched.read_text(encoding="utf-8"), encoding="utf-8"
        )
        for cmd, label in (
            (["git", "add", "README.md"], "git add"),
            (["git", "commit", "-m",
              "docs: fix README drift detected by readme-truth-checker"],
             "git commit"),
            (["git", "push", "-u", "origin", branch], "git push"),
        ):
            res = _run(cmd, repo, timeout=120)
            if res.returncode != 0:
                print(f"ERROR during {label}: {res.stderr or res.stdout}",
                      file=sys.stderr)
                return 6

        pr = _run([
            "gh", "pr", "create",
            "--title", "docs: fix README drift detected by readme-truth-checker",
            "--body-file", str(pr_body),
            "--head", branch,
        ], repo, timeout=120)
        if pr.returncode != 0:
            print(f"ERROR creating PR: {pr.stderr or pr.stdout}", file=sys.stderr)
            return 7

        print(pr.stdout.strip())
        return 0
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 99


if __name__ == "__main__":
    sys.exit(main())
