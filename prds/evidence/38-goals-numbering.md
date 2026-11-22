# Goal #38 — one namespace per kind of number in `GOALS.md`

## The defect

`GOALS.md` §4 opened with `### #36 — Decide the PRD 02–07 test layout…`, written
when §4 was the plan. Later, work that §4 never anticipated took **runner
position #36** for the `prds` gate (goal #36, `prds/evidence/36-prds-gate.md`).
From that day "`#36`" denoted two different goals:

```
$ grep -rn 'goal #36\|#36' GOALS.md prds/evidence/36-prds-gate.md | head
GOALS.md:145:### #36 — Decide the PRD 02–07 test layout, then make every §10 preamble name a real path
prds/evidence/36-prds-gate.md:1:# Goal #36 — gate the consistency checker
```

Nothing failed. Every number was well-formed, every table lined up, every
`§`-reference resolved, and `prds/check_consistency.py` was green — the check
that exists to find broken citations could not see a citation that pointed at
*two* goals. That is the same class of defect the `owners` check was written
for (a bare number drifting off the goal it named), one level up: the numbering
rule itself was a promise in prose, not a property the file has.

## What changed

| File | Change |
| --- | --- |
| `GOALS.md` | §3 "Carried" widened from `#23–#37` with verified rows for #36 (the `prds` gate) and #37 (the lint scope); §4 retitled "programme items **P1–P12**, none of them queued yet" and its twelve headings renumbered `#36–#46` → `P1–P12`; §5's dependency graph renumbered to `P1…P12`; the status header rewritten to drop the old "goal numbers are stable" claim and to state the two-namespace rule and what the checker now enforces; the closing note no longer cites a position it cannot show |
| `prds/check_consistency.py` | `_goal_texts()` stops parsing `### #NN` programme headings and handles `PN` items plus queued `#NN`; it is now a projection of `_registry_entries()`, which returns every §2/§3 registration **with duplicates preserved**; `check_owners()` validates both halves of every baseline reference *and* owns the file's numbering (`_check_goal_numbering`); the `owners` summary line reports `'#NN'` and `'P<n>'` references separately |
| `tests/unit/test_goal_numbering_contract.py` | nine tests that drive `_check_goal_numbering` over synthetic plans, so each rule is pinned by the plan it rejects instead of by the current corpus happening to trip it; the real `GOALS.md` is never touched |

The rule, as the checker now enforces it:

* a `#NN` is *registered* by a row in §2 or §3; a `### #NN` heading may not
  reuse a registered number (that is the collision above);
* §4's headings are `PN`, and no heading outside §4 may be one — a queued goal
  carries the runner position it was queued under;
* every `#NN` and every `PN` written anywhere in the file resolves to an id the
  file defines, so a citation can never point at nothing.

`PN` items are deliberately absent from `_goal_texts()`: they hold no runner
position yet, so a baseline reference may not point at one with `#NN`. Their own
resolution is `_check_goal_numbering`'s job.

## Drills — each mutation must fail, and does

```
--- DRILL A: P1 heading renamed back to a runner position inside §4
exit=1
  FAIL [owners] GOALS.md: '### #36 — Decide the PRD 02–07 test layout…' sits in §4,
       whose items are programme items: it must be 'P<n>', not a runner position
  FAIL [owners] GOALS.md: reference 'P1' names no programme item in §4

--- DRILL B: carried row 37 renumbered onto the occupied number 36
exit=1
  FAIL [owners] GOALS.md: reference '#37' names no goal registered in §2/§3

--- DRILL C: programme item P7 heading deleted
exit=1
  FAIL [owners] GOALS.md: reference 'P7' names no programme item in §4   (×3)

--- DRILL D: a queued '### #36' heading outside §4 reusing a registered number
exit=1
  FAIL [owners] GOALS.md: '### #36 — a second goal claiming the prds-gate position'
       reuses a number §2/§3 already give to '`prds/check_consistency.py` is gated…'

--- DRILL E: restored file
exit=0
```

The tables had a second door, found by adversarially re-verifying this goal after
the first commit: a position registered **twice inside §2/§3** was accepted,
because `_goal_texts()` built a dict and the later row silently overwrote the
earlier one — the same "one number, two goals" defect, in the registry rather
than in the headings. `_registry_entries()` now preserves duplicates and
`_check_goal_numbering()` fails on a second registration:

```
--- DRILL F: a second '| 30 | **verified** |' row in §3
exit=1
  FAIL [owners] GOALS.md: position #30 is registered twice in §2/§3 —
       as '"Zero broken relative links" is a gate: `scripts/check_docs_contract.py` …'
       and as 'a second goal claiming position 30'

--- DRILL G: §2's '| #22 |' row widened to '| #22–#23 |', so §3's #23 is claimed twice
exit=1
  FAIL [owners] GOALS.md: position #23 is registered twice in §2/§3 —
       as 'Final conformance pass, all five interpreters, ratchet raised'
       and as '`DIContainer.scope` returns `-> ResolutionScope`; …'

--- DRILL H: restored file
exit=0
```

`tests/unit/test_goal_numbering_contract.py` pins all of it, and the pin is
load-bearing: deleting the duplicate-registration rule from the checker fails
exactly two of its nine tests (`an overlapping range row was accepted`,
`assert []`), and restoring it turns them green again.

Drill A is the original defect reproduced: put §4's old heading back and the
check names both halves — the heading is in the wrong namespace and the `P1`
references it used to satisfy now point at nothing.

## Verified on the committed tree

```
$ python prds/check_consistency.py
  ok   owners: 26 baseline owner reference(s) resolve to the goal that closed them;
       70 '#NN' and 74 'P<n>' reference(s) in GOALS.md resolve inside one namespace

all checks passed

$ ruff check prds && ruff format --check prds
All checks passed!
1 file already formatted

$ python scripts/check_docs_contract.py
OK: 15 documentation file(s) satisfy the contract (50 relative link(s) resolved)

$ python -m pytest -q
580 passed

$ nox -s ci
ci: all sessions passed
```

The baseline figure is unchanged at 26 references — the `owners` check was
widened, not loosened, and no existing reference had to be re-pointed. The
`'#NN'` count moved 68 → 70: one fell away because the closing note no longer
cites a position it cannot show, and three were added by the #38 row that
registers this goal (its own `#36`, `#37` and `#38` citations).

## What this goal does *not* claim

It does not mirror the live queue here. A position is written into `GOALS.md`
when it is closed (with its evidence) or when it is a programme item; the queue
remains the only source of truth for what runs next, and the checker now fails
on any `#NN` this file cites without registering it, so a second copy of the
queue cannot drift back in unnoticed.
