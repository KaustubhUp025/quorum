## Summary

<!-- What does this PR do? One paragraph is fine. -->

## Type of change

- [ ] Bug fix
- [ ] New rule (Rule N: _____________)
- [ ] Improvement to an existing rule
- [ ] Documentation
- [ ] Infrastructure / CI
- [ ] Other: ___________

## Checklist

### All PRs
- [ ] Tests pass locally (`pytest`)
- [ ] Linting passes (`ruff check src/ tests/`)
- [ ] No credentials or API keys in the diff

### New rules (Rule 9+)
- [ ] Created `src/quorum/rules/rule_NN_name.py` following the existing pattern
- [ ] Added `tests/fixtures/bad/rule_NN_*.py` — a code sample that **should** be flagged
- [ ] Added `tests/fixtures/good/rule_NN_*.py` — a code sample that **should not** be flagged
- [ ] Added surface-detector assertion in `tests/test_detector.py`
- [ ] Added entry to `docs/RULES.md` with wrong/right examples
- [ ] Updated the rules table in `README.md`
- [ ] Added entry to `CHANGELOG.md` under `Unreleased`

### Bug fixes
- [ ] Added a test that would have caught the bug (regression test)
- [ ] Added entry to `CHANGELOG.md` under `Unreleased`

## Testing notes

<!-- How did you test this? Any edge cases? False-positive risk? -->

## Related issues

Closes #
