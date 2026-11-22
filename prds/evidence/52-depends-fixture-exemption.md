# 52 — the `Depends[...]` fixture exemption is bounded at both ends

## The defect

Goal #44 gave `tests/docs/test_depends_spelling.py` a python-source half: every module under
`lauren_di/`, `tests/`, `examples/` and `scripts/` is parsed with `ast`, and a `Depends[...]`
read as a *type* is a violation. The field gate's own fixtures in `tests/unit/test_resolution.py`
must hand the gate that exact shape, so #44 added a line-local marker: `# field-gate fixture`.

The marker was honoured on the line alone, with no bound on *where* the line could be. Measured
on `23cf9ea`, appending this to the shipped package:

```text
class _Probe:
    x: Depends[Repo]  # field-gate fixture
```

to `lauren_di/_custom.py` left the module green:

```
$ python -m pytest tests/docs/test_depends_spelling.py -q
6 passed in 1.52s
```

So the one exemption could be pasted into `lauren_di/`, into an example a reader copies, or onto
any clean line in any scanned file, and the guard would stay silent — the same shape #44 exists
to catch, reachable through the exemption rather than around it. Goal #52's "cannot grow
silently" clause is exactly this: a test asserted that every marker exempted *something*, but
nothing bounded the set of files that could carry one.

## The fix

The exemption now needs **both** conditions, in `_marked()`:

* the comment sits on the offending line (unchanged — it exempts nothing else), **and**
* the file is one of `MARKER_FILES`, a one-entry list (`tests/unit/test_resolution.py`).

Either half alone is too weak: the marker alone exempts shipped code, and a file allow-list
alone exempts a whole file (what #44 rejected). Together they are precise (one line) and bounded
(one fixture). Growth now costs a reviewed edit to `MARKER_FILES`, and the list is itself
falsifiable — the test asserts it is non-empty, that every entry sits under `MARKER_FILES`'
`tests/` root, that the file exists, and that it still carries a marker it actually needs, so an
entry cannot go stale or drift into the package.

## Probes

Each probe appended its payload to the named file and restored it afterwards
(`git status --porcelain` empty between runs).

| Probe | Before (`23cf9ea`) | After |
| --- | --- | --- |
| marked annotation in `lauren_di/_custom.py` | **6 passed** (silent) | 2 failed — `test_no_python_module_puts_depends_in_annotation_position`, `test_the_marker_exempts_one_line_and_is_still_in_use` |
| unmarked annotation in `lauren_di/_custom.py` | 1 failed | 1 failed |
| marked annotation in `examples/01_minimal_singleton.py` | **6 passed** (silent) | 2 failed |
| `# field-gate fixture` sprayed onto a clean line in `tests/unit/test_public_api.py` | 1 failed | 1 failed |
| a second marked annotation in the listed fixture file | 6 passed | 6 passed (the exemption still works where it is justified) |

The first and third rows are the regression this goal closes; the last row is the control that
the fix did not simply forbid the marker.

## Verification

| Check | Result |
| --- | --- |
| `python -m pytest tests/docs/test_depends_spelling.py -q` | 6 passed |
| `test_the_source_scan_reports_what_it_read` | `scanned 70 python module(s) under lauren_di, tests, examples, scripts` |
| `python -m pytest -q` | 647 passed (no test added, so `AGENTS.md`'s 595/569 pair is unchanged) |
| `nox -s lint format` | clean (`ruff check` all passed, 71 files formatted) |
| `python prds/check_consistency.py` | exit 0, all ten `ok` lines |

## Registry

Row 52's text cites goal #44, and that citation was the first in `GOALS.md` to name a position in
#44–#48 — which turned the `owners` check red: five closed goals had evidence pages
(`44-depends-spelling-python.md` … `48-prds-evidence-gate.md`) and commits, but no row in §3, so
nothing in the anchor document registered them. Widening §3's heading to `#23–#52` made the same
point from the other side: the heading then claimed five positions the table did not have, and
`prds/check_consistency.py` named each one (`reference '#44' names no goal registered in §2/§3`).

All five are now registered from their evidence pages, so the heading, the rows and the citations
agree: **43** §2/§3 data rows, **52** runner positions, **0** unreadable, and 89 `#NN` references
resolving inside one namespace.
