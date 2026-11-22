# 51 — the 3.10 rule is stated, not typed

## The defect

`expected_count()`'s docstring is the only place that explains *why* the quick-start
line carries two figures. It said:

> ``tomllib`` arrived in 3.11, so ``tests/unit/test_readme_contract.py`` is not
> collected on 3.10 and the total is **25** lower there.

Measured on `9fcd6bd`:

```
$ python -m pytest --collect-only -q -p no:cacheprovider tests/unit/test_readme_contract.py
26 tests collected in 0.24s
```

The module collects **26**, and the documented pair was 594/568 — a difference of 26
in both cases. So the docstring typed a figure that was wrong by one, and it sat
*inside* the very guard that exists to catch stale figures:
`test_the_3_10_figure_is_the_interpreter_without_tomllib` asserts
`modern - legacy == _collected("tests/unit/test_readme_contract.py")` and passed at
26 while the prose beside it said 25. Same failure class as goals #31 and #41: a
well-formed claim that no mechanism reads.

## The fix

The relation is stated; the figure is measured.

* `expected_count.__doc__` now says the legacy figure is lower "by exactly that
  module's collection count — as many tests as `test_readme_contract.py` collects,
  whatever that number is", and points at the sibling test that measures it. The
  only numerals left in it are interpreter versions (`3.11`, `3.10`).
* `test_the_3_10_figure_is_the_interpreter_without_tomllib` keeps measuring the
  relation, unchanged.
* A new `test_the_3_10_rule_is_stated_without_a_typed_count` makes the docstring a
  claim the suite reads:

```python
doc = expected_count.__doc__ or ""
assert "test_readme_contract.py" in doc and "tomllib" in doc
without_versions = re.sub(r"\b3\.\d+\b", "", doc)
assert not re.search(r"\d", without_versions)
```

Both halves of the rule must stay named (so the docstring cannot be emptied into
something unfalsifiable), and any digit that is not an interpreter version fails —
so a typed figure cannot come back, whatever value it carries.

## Before / after

Each probe ran on the fixed tree and the file was restored afterwards:

```
$ python - <<'PY'   # put the old sentence back: "... and the total is 25 lower there."
...
$ python -m pytest tests/unit/test_documented_test_count.py -q
FAILED tests/unit/test_documented_test_count.py::test_the_3_10_rule_is_stated_without_a_typed_count
1 failed, 5 passed
```

```
$ python - <<'PY'   # a *correct* typed figure, to prove the rule is about the shape: "the difference is 26 tests."
...
$ python -m pytest tests/unit/test_documented_test_count.py -q
FAILED tests/unit/test_documented_test_count.py::test_the_3_10_rule_is_stated_without_a_typed_count
1 failed, 5 passed
```

The second probe is the point: the guard rejects the *form*, not one bad value, so it
does not need re-typing when the module grows.

## Verification

| Check | Result |
| --- | --- |
| `python -m pytest tests/unit/test_documented_test_count.py -q` | 6 passed |
| restoring the literal "25" | the new test fails |
| a correct but typed "26 tests" | the new test fails |
| `python -m pytest -q` | 647 passed (595 without the parity checkout; 569 on 3.10) |
| `AGENTS.md` quick-start count | 594 / 568 / +52 → **595 / 569 / +52** (this module gained one test), re-measured with `--collect-only` on 3.13 and 3.10 |
| `nox -s lint format` | clean (`ruff check` all passed, 71 files formatted) |
| `nox -s ci` | green — `ci: all sessions passed` |
