# Goal 44 — the `Depends[T]` spelling guard, extended from markdown to python sources

**Question.** `tests/docs/test_depends_spelling.py` scanned `prds/**/*.md`, `README.md`,
`docs/**/*.md`, `llms.txt` and `llms-full.txt`. It reported *no offenders* while
`lauren_di/_container.py`'s module docstring and `tests/parity/test_matrix.py` both spelled a
dependency in annotation position, because neither is a markdown file. Was the rule enforced,
or only the part of the corpus the guard happened to read?

## What was measured, on `c873fff` before the change

An AST walk over `lauren_di/**/*.py` and `tests/**/*.py`, marking every `Subscript` whose
value is `Name("Depends")` and that sits inside an annotation a checker reads
(`AnnAssign.annotation`, parameter and return annotations):

```
files: 54
tests/unit/test_resolution.py:46: Depends[make_repo_factory]
tests/unit/test_resolution.py:56: Depends[Repo]
```

The two files the goal named as offenders had **already been corrected** by goal 43's
docstring rewrite and goal 28's fixture rewrite, so the markdown-only guard's "no offenders"
was, at that moment, true of the files it read and false of the corpus. The two remaining
hits are the field gate's own *input*: a test cannot show that the gate skips a non-type token
without handing it one in annotation position.

A second walk, over `examples/**` and `scripts/**` — not in the goal's scope — found one
genuine, shipped defect the goal did not know about:

```
examples/06_injection_styles.py:54: Depends[Clock]
```

Example 6's *style 2* taught `clock: Depends[Clock]` as the class-body form. That is exactly
the spelling PRD 01 §5.5 forbids, in the file a reader copies. It is corrected here.

## The guard, as extended

`tests/docs/test_depends_spelling.py` now reads the corpus two ways with one detector
(`spellings()`, unchanged):

| layer | corpus | why |
| --- | --- | --- |
| fenced blocks | `prds/**/*.md`, `README.md`, `examples/README.md`, `docs/**/*.md`, `llms.txt`, `llms-full.txt` | what the docs contract compiles (§10) |
| python modules | `lauren_di/**`, `tests/**`, `examples/**`, `scripts/**` | the package's own docstrings and the fixtures that write real Python |

The module layer covers both real code (`ast.parse` of the file) and the python examples a
docstring shows: a reST literal block introduced by `::` is dedented, parsed, and scanned, and
its line numbers are mapped back to the file. A literal block that does not compile is prose or
a shell transcript (`.. note::` directives, `mypy …` command blocks in `tests/typing/`), so it
contributes nothing.

`prds/evidence/**` is outside both layers by design: throwaway probes, cited by id, audited by
`prds/evidence/run_all.py --check`.

## The one exemption, and why it is a marker rather than a path

The field-gate fixtures must keep their annotation. A path allow-list would exempt the whole
file and could widen unnoticed, so the exemption travels with the line instead:

```text
@injectable()
class SpelledTypeField:
    repo: Depends[Repo]  # field-gate fixture
```

`test_the_marker_exempts_one_line_and_is_still_in_use` asserts the marker works in both
directions (synthetic module, synthetic docstring), that a live use still exists (so the
exemption cannot go dead), and that every live use sits on a line the scanner reports once the
marker is stripped (so it cannot be sprayed onto clean lines). The marker is found with
`tokenize` — comments only — so this document and the module's own prose can name it.

## Drills

Each drill was run, observed, and reverted.

| drill | expected | observed |
| --- | --- | --- |
| append `class _Drill: repo: Depends[Repo]` to `lauren_di/_custom.py` | the module test fails, naming the file and the count scanned | `AssertionError: … in 68 scanned python module(s) … ['lauren_di/_custom.py:504: Depends[Repo]']` |
| restore `_custom.py` | green, `git diff --stat lauren_di/` empty | `6 passed`; no diff |
| `examples/06_injection_styles.py` as it stood | the module test fails | `examples/06_injection_styles.py:54: Depends[Clock]` |
| `violations(UNMARKED_MODULE)` / `violations(MARKED_MODULE)` | one finding / none | one finding at line 3 / none |
| `violations(DOCSTRING_MODULE)` / marked variant | one finding at the docstring's own line / none | line 7 / none |

## Result

`python -m pytest tests/docs/test_depends_spelling.py -q` → 6 passed, 68 python modules and
213 fenced blocks scanned. Full suite 603 passed (551 without `tests/parity`), so AGENTS.md's
quick-start figures moved 548/522 → 551/525.

`nox -s lint format docs_contract llms_check prds` → all green.
