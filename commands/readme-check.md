---
description: Validate the root README.md by running its steps in a clean Docker container. Python projects only. Reports drift, proposes fixes, asks before opening a PR.
argument-hint: "[optional path to a Python repo; defaults to current directory]"
---

# /readme-check

You are running the README Truth Checker pipeline. Your job is to detect README drift, diagnose it, and propose a verified fix — without modifying anything the user hasn't approved.

## Inputs

- `$ARGUMENTS`: optional path to a Python repo. If empty, use the current working directory.

## Pipeline (run in order, stream a one-sentence progress note before each phase)

### Phase 0 — Prerequisites
- Verify the repo directory exists and contains `README.md` at its root. If not, stop and tell the user.
- Verify `python` (3.11+) is on PATH. If not, stop.
- Verify `docker` is on PATH **and the daemon is running** (try `docker info`). If not, stop with a clear message.

### Phase 1 — Extract steps
Run:
```
python "${CLAUDE_PLUGIN_ROOT}/scripts/extract_steps.py" "<repo>/README.md"
```
Save the JSON output to a temp file. If `step_count` is 0, tell the user "README contains no testable shell steps" and exit.

### Phase 2 — Detect Python project
Run:
```
python "${CLAUDE_PLUGIN_ROOT}/scripts/detect_python_project.py" "<repo>"
```
If `is_python` is `false`, stop and tell the user this plugin only supports Python projects in v1.

### Phase 3 — Execute in clean room
Run:
```
python "${CLAUDE_PLUGIN_ROOT}/scripts/runner.py" "<repo>" "<project.json>" "<steps.json>"
```
This may take several minutes the first time (Docker build). Tell the user that before kicking it off.

### Phase 4 — Branch on results
Read the `steps[]` array in the runner output:
- If every step is `passed` or `skipped`: print `✅ README is accurate — <N> steps verified, <M> skipped (placeholders)` and exit.
- Otherwise: continue.

### Phase 5 — Diagnose
Run:
```
python "${CLAUDE_PLUGIN_ROOT}/scripts/diagnose.py" "<repo>" "<results.json>"
```

### Phase 6 — Generate proposed fix
Run:
```
python "${CLAUDE_PLUGIN_ROOT}/scripts/generate_fix.py" "<repo>/README.md" "<drift_report.json>"
```
The output contains the patched README, a unified diff, and a list of notes. **Nothing has been written to disk in the repo yet.**

### Phase 7 — Show the user
Print a clear report:
1. Summary line: `Found <N> drift point(s) across <M> failed step(s).`
2. For each drift point: failing step, cause, evidence (with file:line citations and git commit if known), proposed fix.
3. The full unified diff in a fenced code block.

### Phase 8 — Ask via AskUserQuestion

Question: `Open a PR with these README fixes?`
Header: `Open PR?`
Options:
- `Yes, open the PR` — re-verify the fix and create a PR via gh CLI
- `No, just show me the suggestions` — exit without changing anything

### Phase 9 — Branch on the answer

**If YES:**
1. Write the patched README to a temp file.
2. Re-run Phase 3 against the patched README (build a temporary repo copy with the patched file, run `runner.py` again).
3. If re-verification fails — tell the user the proposed fix doesn't actually go green, do NOT open a PR, exit.
4. If re-verification passes:
   a. Render the PR body from `${CLAUDE_PLUGIN_ROOT}/templates/pr-body.md`, substituting the drift summary, evidence block, and verification results.
   b. Run:
      ```
      python "${CLAUDE_PLUGIN_ROOT}/scripts/open_pr.py" "<repo>" "<patched_readme>" "<pr_body>"
      ```
   c. Print the PR URL from the script's stdout.

**If NO:**
Print the diff one more time clearly, say "No changes made," exit.

## Hard rules

- **Never modify the user's working tree** until they pick YES.
- **Never open a PR** until the patched README has been re-verified green in a clean container.
- **Never claim success if Docker wasn't available** — surface the prerequisite failure plainly.
- Stream concise progress updates between phases. One sentence each.
- Do not retry failed steps. One run, one verdict.
