# Goal #39 — PRD 03 §5.3 and `ResolutionScope` name the same members

## The defect

PRD 03 §5.3 documented the request-scope object with two member names the class
does not have, and its runnable-looking example asserted one of them:

```
$ git show 3cb1c16:prds/03-scopes-lifecycle-and-disposal.md | sed -n '276,306p'
| `active` | `bool` | `True` only between `__aenter__` and close |
| `teardown_errors` | `tuple[DestructError, ...]` | Empty until the scope closes; … |
…
    # leaving the block tore down UnitOfWork, newest first
    assert scope.teardown_errors == ()

$ python -c 'from lauren_di import ResolutionScope; print(sorted(n for n in dir(ResolutionScope) if not n.startswith("_")))'
['close', 'closed', 'errors', 'resolve', 'values']
```

Every gate was green. The block is valid Python, so it *compiles*;
`scripts/check_docs_contract.py` compiles blocks and stops there, so nothing
called it. A reader who typed the example out met
`AttributeError: 'ResolutionScope' object has no attribute 'teardown_errors'`,
and a reader who trusted the table inverted the sense of the flag: the shipped
member is `closed`, whose value is the opposite of `active`'s.

That is the same shape as goal #38's defect — a documented promise no
mechanism reads — one layer down: not a number that names two goals, but a name
that names no attribute.

## What changed

| File | Change |
| --- | --- |
| `prds/03-scopes-lifecycle-and-disposal.md` | §5.3's example rewritten so it *runs* (fenced `python run`) and asserts `scope.closed is True`, `scope.errors == ()` and that the teardown actually happened (`uow.tx.rolled_back is True`), which the old example never proved; the surface table's two rows renamed `active` → `closed` and `teardown_errors` → `errors`, with the inversion stated in the contract cell; §1.6, §5.3's prose, §5.7's table and §6.4's rows re-pointed at `errors`; the four remaining `teardown_errors` mentions are notes that name the *reference's* spelling |
| `prds/README.md` | the `ResolutionScope` bullet no longer claims `teardown_errors` |
| `tests/docs/prd_examples.py` | the §5.4 engine generalised over a `Section` (`path`, `start`, `end`) so PRD 03 §5.3 can be driven by the same `collect` / `compile` / `execute` path; `collect_section`, `subsections_of`, `inventory_of` added |
| `tests/docs/test_prd_03_examples.py` | seven tests over §5.3: the pinned inventory, every block compiles, every `run` block executes, the surface table equals `dir(ResolutionScope)`, the `closed` row states the shipped sense, no prose names a member the class lacks, and the execution step is proved load-bearing |

The direction chosen is **amend the PRD to the shipped names**, not rename the
class. PRD 01 §5.3.1 fixes `.closed`/`.errors` as the public surface and PRD 01
§5.4's example asserts them, so renaming would break a documented, tested API to
match a document that had drifted from it (§6.4 row 11 records the decision).

## Drills — each mutation must fail, and does

```
$ cp prds/03-….md /tmp/prd03.bak   # restore between drills

--- DRILL A: `scope.errors` in the runnable block put back to `scope.teardown_errors`
pytest_exit=1
  FAILED test_every_run_block_executes[03-scopes-lifecycle-and-disposal.md:278]
  FAILED test_no_prd_03_prose_names_a_scope_member_the_class_lacks
  AssertionError: PRD 03 names scope members the class does not ship: ['teardown_errors']

--- DRILL B: the surface table's `closed` row renamed back to `active`
pytest_exit=1
  FAILED test_the_surface_table_names_exactly_the_shipped_members
  FAILED test_the_closed_row_states_the_shipped_sense   (KeyError: 'closed')

--- DRILL C: prose only — "`errors` and `close()` typed" → "`scope.teardown_errors` and `close()` typed"
pytest_exit=1
  FAILED test_no_prd_03_prose_names_a_scope_member_the_class_lacks
  (the block test still passes: the sentence is the third way in, and it has its own check)

--- DRILL D: the table's `errors` row renamed back to `teardown_errors`
pytest_exit=1
  FAILED test_the_surface_table_names_exactly_the_shipped_members
  (only in the table ['teardown_errors'], only in the class ['errors'])

--- DRILL E: the example degraded from `python run` to `python skip`
pytest_exit=1
  FAILED test_the_section_has_the_block_this_module_asserts_on
  1 failed, 4 passed, 2 skipped
  (§5.3 must carry a `run` block: a compile-only example is how the
   `teardown_errors` defect survived)

--- DRILL F: restored file
pytest_exit=0
  git status --porcelain shows only the intended edits
```

Drill C is the one that matters most: it proves the prose check is not a
restatement of the execution check. The sentence sits *outside* any block, so
the `run` block would never have caught it — that is exactly the hole the
original defect fell through.

## The allowlist the prose check needed

`SCOPE_MEMBER_RE` is `(?<![\w.])scope\.(?!md\b)([a-z_][a-z0-9_]*)`. The first
draft matched `consumer.scope.label` at PRD 03 lines 263–264, where `scope` is
the `Scope` **enum** (its `.label` is the `"req"`/`"singleton"` spelling), not a
`ResolutionScope`. The lookbehind requires the name not to be preceded by a dot,
so an attribute of something called `scope` is not read as a member of the scope
object; the lookahead drops `docs/reference/scope.md`, a page name that would
otherwise be read as a member called `md`. Neither exclusion is a member
allowlist: no shipped-or-not decision is waived, only the two shapes that are
not member references at all.

## Verified on the committed tree

```
$ python prds/check_consistency.py
  ok   links: every relative markdown link resolves
  ok   cross-refs: 129 'PRD 0N §x' references checked
  ok   decisions: D-1…D-56 defined, 56 rows
  ok   symbols: shutdown/Depends/surface/resolve/overrides/scopes/Provider all agree
  ok   scope: no web-framework imports; unguarded `import lauren` samples: 0
  ok   owners: 26 baseline owner reference(s) …; 70 '#NN' and 74 'P<n>' reference(s) …

all checks passed

$ python scripts/check_docs_contract.py
OK: 15 documentation file(s) satisfy the contract (50 relative link(s) resolved)

$ python -m pytest -q
587 passed

$ nox -s ci
ci: all sessions passed
```

`AGENTS.md`'s quick-start count moves 528 → 535 (502 → 509 on 3.10): the seven
new tests, nothing else. The `'#NN'` count moves 70 → 71 — the §3 row that
registers this goal cites its own position; the `'P<n>'` figure and the 26
baseline owner references are unchanged.

## What this goal does *not* claim

It does not claim §5.3 is the only place the PRD names scope members. The
`scope.<member>` check reads the whole of PRD 03, but a member named in *prose
without* the `scope.` prefix (`teardown_errors`, bare) is invisible to it — that
is why §1.6, §5.7 and §6.4 were re-pointed by hand, and why the four surviving
`teardown_errors` mentions were read one by one and kept only where they name
the reference's spelling. A future rename has to be made in those places too;
the check catches the drift only once it is written as a `scope.` reference.
