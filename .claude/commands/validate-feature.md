# /validate-feature

Validate that a named feature works end-to-end, then update `.context/` files if it passes.

## What this command does

### Step 1 — Identify the feature

If $ARGUMENTS is provided, use it as the feature name (e.g. "issue-filer", "audit-log", "multi-language").
If empty, look at `git log --oneline -5` and the `⬜` sections in `05_next_steps.md` to determine what was most recently added.

### Step 2 — Run the test suite

```bash
python3 -m pytest --tb=short -q
```

If any tests fail: stop immediately, report the failures, and do NOT update the context files.

### Step 3 — Run feature-specific validation

Based on the feature name, run the appropriate smoke test:

**audit-log / quorum history:**
```bash
QUORUM_AUDIT_LOG=/tmp/quorum_validate_$$.json python3 -c "
from quorum.models import Finding, ReviewResult, Severity
from quorum.audit_log import append_entry
f = Finding(rule_id='RULE_06', rule_name='Test', severity=Severity.HIGH,
            confidence=90, title='Validate', explanation='...')
r = ReviewResult(mr_iid=1, project_id='test/validate', findings=[f])
e = append_entry(r, platform='github')
assert e.high == 1
print('audit-log OK:', e.repo, e.high, 'high finding')
"
python3 -m quorum history --json 2>/dev/null | python3 -c "import json,sys; print('history OK:', len(json.load(sys.stdin)), 'entries')"
```

**issue-filer / file-issue:**
```bash
python3 -m quorum file-issue --help
python3 -m pytest tests/test_issue_filer.py -v --tb=short
```
Then dry-run against the most recent audit log entry:
```bash
python3 -c "
from quorum.audit_log import load_entries
entries = load_entries()
if entries:
    e = entries[-1]
    print(f'Dry-run target: {e.repo} PR #{e.pr}')
"
```

**multi-language:**
```bash
python3 -c "
from quorum.detector import detect_surfaces
cases = [
    ('Go retry', '+\ttime.Sleep(5 * time.Second)\n+\tfor attempt := 0; attempt < 3; attempt++ {'),
    ('JS retry', '+  setTimeout(() => retry(), 5000)'),
    ('kafkajs', '+  autoCommit: true,'),
    ('Go redis', '+\tok, _ := rdb.SetNX(ctx, key, \"locked\", ttl).Result()'),
]
for label, diff in cases:
    rules = [r.id for r in detect_surfaces(diff)]
    status = '✅' if rules else '❌'
    print(f'{status} {label:20s} → {rules}')
" 2>/dev/null
```

**file-issue / create_issue clients:**
```bash
python3 -c "
from quorum.github_client import GitHubRESTClient
from quorum.gitlab_client import GitLabRESTClient
# Verify methods exist (not just attribute access — check they're coroutines)
import inspect
assert inspect.iscoroutinefunction(GitHubRESTClient.create_issue)
assert inspect.iscoroutinefunction(GitHubRESTClient.check_repo_metadata)
assert inspect.iscoroutinefunction(GitLabRESTClient.create_issue)
assert inspect.iscoroutinefunction(GitLabRESTClient.check_repo_metadata)
print('Client methods OK: create_issue + check_repo_metadata on both clients')
"
```

### Step 4 — Update context files on success

If all validation checks pass:
- Run `/sync-context $ARGUMENTS` to update `04_session_log.md` and `05_next_steps.md`

If any check fails:
- Print the failure clearly
- Do NOT update context files
- Suggest what to fix

## Usage

```
/validate-feature issue-filer      # validate issue-filer, update context if OK
/validate-feature audit-log        # validate audit log feature
/validate-feature multi-language   # validate Go/JS/Ruby surface patterns
/validate-feature                  # auto-detect from recent commits
```

## Output format

```
Feature: <name>
Tests:   151/151 ✅
Checks:  3/3 ✅
  ✅ issue-filer dry-run: rule-09 CRITICAL + rule-06 HIGH shown
  ✅ create_issue method on GitHubRESTClient
  ✅ check_repo_metadata method on GitLabRESTClient
Context: Updated — Step 48 ✅ in 04_session_log.md, 1 section ⬜→✅ in 05_next_steps.md
```
