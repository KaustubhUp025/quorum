# Contributing a new rule to Quorum

Each coordination anti-pattern is a standalone rule module. Adding Rule 9 is a ~50-line contribution that does not require touching any existing code.

---

## Step 1 — Create the rule module

Create `src/quorum/rules/rule_09_your_rule_name.py`:

```python
from quorum.rules.base import Rule

RULE = Rule(
    # Unique identifier — increment from the last rule in the registry.
    id="RULE_09",

    # Short human-readable name (shown in tables and comment headers).
    name="Your Rule Name",

    # Full description of the anti-pattern: what it is, why it matters,
    # what "wrong" looks like vs "right". Used in the Gemini reasoning prompt.
    description=(
        "One or two sentences describing the coordination failure mode. "
        "Include: what the wrong pattern is, what failure it causes, "
        "and what the correct pattern looks like."
    ),

    # Canonical reference (name and URL shown in MR comment findings).
    reference="Author — Title of the reference",
    reference_url="https://example.com/canonical-reference",

    # Keywords for the fast pre-filter (case-insensitive substring match).
    # Include: API names, annotation names, config keys, common variable names.
    # Be inclusive — false positives here are cheap; false negatives are not.
    surface_keywords=[
        "keyword_one",
        "keyword_two",
    ],

    # Regex patterns for the pre-filter (applied after keyword match).
    # Leave empty to rely on keywords alone.
    surface_patterns=[
        r'your\.pattern\s*\(',
    ],

    # Natural-language queries sent to semantic_code_search during investigation.
    # These guide Gemini's cross-repo context gathering.
    # 2–4 queries is ideal: too few misses context; too many wastes tokens.
    search_query_templates=[
        "description of what to look for across the project",
        "related utility or handler that confirms or refutes the finding",
    ],

    # Guidance for Gemini's reasoning step.
    # Tell it: what to look for in the diff, how to use search results,
    # what constitutes a CRITICAL vs HIGH vs MEDIUM finding.
    reasoning_guidance=(
        "Explain what Gemini should check in the diff and what to look for "
        "in the search results. Specify the severity threshold: "
        "Flag CRITICAL when X. Flag HIGH when Y. Flag MEDIUM when Z."
    ),
)
```

The registry auto-discovers any file named `rule_*.py` that exports a `RULE` instance.

---

## Step 2 — Add test fixtures

**Bad code** (should trigger the rule): `tests/fixtures/bad/rule_09_your_case.py`

```python
"""BAD: <one-line description of what's wrong>."""

# A realistic but minimal code sample that exhibits the anti-pattern.
# Use the actual language/framework the rule targets.
```

**Good code** (should NOT trigger a finding): `tests/fixtures/good/rule_09_correct_case.py`

```python
"""GOOD: <one-line description of the correct pattern>."""

# The same scenario implemented correctly.
```

---

## Step 3 — Add a detector test

Add to `tests/test_detector.py`:

```python
def test_your_pattern_triggers_rule_09(self, bad_fixture):
    diff = bad_fixture("rule_09_your_case.py")
    triggered = detect_surfaces(diff)
    ids = [r.id for r in triggered]
    assert "RULE_09" in ids

def test_correct_pattern_does_not_produce_false_negative(self, good_fixture):
    # Note: the detector CAN trigger on good code (it's a pre-filter).
    # The Gemini reasoning step is what suppresses false positives.
    # This test is optional but useful to document intent.
    pass
```

---

## Step 4 — Run the tests

```bash
pytest tests/test_detector.py -v
```

All existing tests must still pass. Your new test must pass.

---

## Step 5 — Open a PR

- **Title:** `feat(rules): add RULE_09 — Your Rule Name`
- **Description:** Include the canonical reference, a "bad" code sample, and a "good" code sample.
- The CI pipeline will run Quorum on your own MR. Meta.

---

## Rule quality checklist

- [ ] `surface_keywords` are inclusive enough that the bad fixture triggers the detector
- [ ] `search_query_templates` are specific enough to find related code in a real project
- [ ] `reasoning_guidance` specifies clear severity thresholds (CRITICAL / HIGH / MEDIUM)
- [ ] `reference` links to an expert-validated source (not just a blog post)
- [ ] Bad fixture exhibits exactly the anti-pattern described (not a strawman)
- [ ] Good fixture shows the correct pattern (not just the absence of the bad pattern)

---

## Ideas for new rules

Rules with expert documentation and no existing LLM tooling to detect them:

- **RULE_09** — Two-phase commit without a coordinator timeout (split-brain on coordinator crash)
- **RULE_10** — Distributed deadlock via lock ordering inconsistency across services
- **RULE_11** — Cache stampede: no mutex / probabilistic early expiry on popular keys
- **RULE_12** — Event sourcing: mutable aggregate without snapshot compaction guard
- **RULE_13** — Read-your-writes: client reads from replica immediately after write without consistency token
- **RULE_14** — Circuit breaker: fallback value is stale cached data without a staleness timestamp
