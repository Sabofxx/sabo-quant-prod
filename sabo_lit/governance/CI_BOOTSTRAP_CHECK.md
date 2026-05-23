# CI bootstrap check — one-time verification that the build does turn red

The pytest suite proves two things in-process:

* `scan_runner.run_scan` returns the right violation list given a tree.
* `scan_runner.main` turns that list into the right exit code
  (0 / 1 / 2).

What it CANNOT prove is that GitHub Actions actually fails the build on
exit 1 — only GitHub does that. This file documents the one-shot
manual check to confirm it does, the first time CI is enabled.

Do this once, immediately after the first push that activates the
`ci` workflow on the default branch (which should be green — the
production tree is clean).

## Procedure

1. **Pre-condition.** The `ci` workflow has run at least once on the
   default branch and is green. If it is not green at baseline, fix
   that first; the check below depends on a known-green starting state.

2. **Create a throw-away branch.**

   ```bash
   git checkout -b ci-bootstrap-check
   ```

3. **Introduce one deliberate violation.** Open
   `sabo_lit/lit/__init__.py` and add this single line at the very end
   of the file (after the existing docstring):

   ```python
   import sabo_lit.research  # noqa: F401  — CI bootstrap check, do not merge
   ```

   This violates `ForbiddenImportRule` (production module
   `sabo_lit.lit` is forbidden from importing the research zone). The
   scanner will flag it; `main` will return exit code 1.

4. **Commit and push.**

   ```bash
   git add sabo_lit/lit/__init__.py
   git commit -m "ci-bootstrap: deliberate violation, do not merge"
   git push -u origin ci-bootstrap-check
   ```

5. **Open a PR** against the default branch. Wait for the `ci`
   workflow to run.

6. **Verify the result.** The PR check must be RED.

   In the CI logs, two steps should fail:

   * **Run tests** — `test_production_tree_is_clean` fails with a
     reported violation pointing at
     `sabo_lit/lit/__init__.py`.
   * **Run governance scan (single source of truth for the verdict)** —
     prints the violation to stderr and exits 1, marking the job red.

   If EITHER step still shows green, the wiring is broken; do not
   start Phase 1 deliverable 2 until that is resolved.

7. **Clean up.** Close the PR without merging and delete the branch:

   ```bash
   git checkout main                       # or the default branch name
   git branch -D ci-bootstrap-check
   git push origin --delete ci-bootstrap-check
   ```

## Why this lives in `governance/`

The CI verdict is governance. This procedure is the manual edge of the
verdict path that we cannot exercise in-process. Putting the file next
to `scan_runner.py` keeps every artefact tied to the same single
source of truth: change `scan_runner.py`, update this file in the same
commit.
