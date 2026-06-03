# /sync-context

Update the `.context/` session log and next-steps files to reflect features that have been built and validated since the last update.

## What this command does

1. **Discover what changed** — reads `git log` and `git diff` since the last recorded commit in `05_next_steps.md` to identify new commits not yet documented.

2. **Run tests** — executes `python3 -m pytest --tb=short -q` to confirm all tests pass before logging any feature as complete. If tests fail, stop and report the failure instead of updating the log.

3. **Read source files** — for each changed file in the new commits, read the actual implementation to write accurate documentation (method signatures, config keys, test counts).

4. **Identify the right context file** — the mapping is:
   - Work-in-progress features being completed → `04_session_log.md` (add a new Step entry at the bottom)
   - Features listed as `⬜` (planned) that are now done → `05_next_steps.md` (change `⬜` to `✅` and replace planned bullet list with actual delivered details)
   - Test counts, commit hashes, and "last updated" metadata → `05_next_steps.md` header lines

5. **Write the update** — add the new Step entry to `04_session_log.md` following the established format:
   ```
   ### Step N — Feature name ✅ — commit `<sha>`
   
   **Files changed:** list of src/quorum/*.py files added/modified
   **Key additions:** method signatures, config keys, CLI commands
   **Tests:** test file name — N tests. Total: old → new passing.
   **Validated:** paste of the actual CLI output that confirmed it works.
   ```
   And in `05_next_steps.md`, flip any matching `⬜` section to `✅` with the delivered summary.

6. **Verify** — after writing, re-read both files to confirm the `⬜` count decreased and the new Step appears at the correct position.

## Usage

```
/sync-context                    # auto-detect uncommitted context work since last documented commit
/sync-context issue-filer        # focus on the issue-filer feature specifically
/sync-context step-48            # update specifically for Step 48
```

## Rules

- Never mark a feature `✅` unless `pytest` confirms all tests pass in the current run.
- Never invent CLI output — only paste output from an actual command run in this session.
- Keep the session log append-only — do not edit existing Steps, only add new ones.
- The "last updated" date in `05_next_steps.md` must use today's actual date.
- If $ARGUMENTS is given, scope the update to only that feature/step. If empty, scan all undocumented commits.
- After updating, print a one-line summary: "Updated: Step N in 04_session_log.md + flipped X sections ⬜→✅ in 05_next_steps.md".
