# Goal #56 — PRD 02's register, held to its own rows

## The question

Goal #53 fixed three documents that over-claimed the divergence register's size: PRD 08
§5.9, PRD 04 §6.4 and `tests/parity/backends.py`. PRD 02 carried the same two defects and
was left alone, because nothing read it:

1. **§6.4 closed by claiming parity over a divergence.** The sentence read

   > Everything else in §6.2 is parity: same token, same exception class name (modulo the
   > `DIError` hierarchy), same ordering, same silent cases.

   That is false for **R-23**. The reference *injects* a function-token class-body field —
   `field_deps= [('clock', 'make_clock')]` and `clock -> Clock` (`E-409`, `E-409b`, emitted
   by `prds/evidence/prd08/p2.py`) — and `lauren_di` skips it (§5.6 gates 4/6). A silent
   case that the two sides disagree about is a divergence, not parity, and §6.4's eight
   rows contained no function-token row: row 6 covers *extractor markers*, which is the
   `P-6` shape (the reference **raises**), not this one (the reference **resolves**).

2. **§6.6 typed a count its own table contradicted.** It said "the only edits are the three
   divergences in §6.4 plus deleting the extractor branch" while the §6.4 table it names
   had eight rows. The count was written against a three-row snapshot and never re-read —
   the failure class of goals #31, #51 and #53.

Neither was visible to any gate. `prds/check_consistency.py` parsed headings, tables,
citations, decisions and code blocks, but never compared a register's prose with its rows;
`tests/parity` asserts behaviour, not prose.

## The shape

One row added, one sentence qualified, one count removed, and the reason both slipped
through closed.

**`prds/02-core-container-and-provider-registration.md`**

- §6.4 gained **row 9**, in the words goal #53 used for PRD 04's row 10 — the reference
  collects the field and injects the factory's value (`E-409`, `E-409b`), this library skips
  it, `Depends[fn] is fn` (D-25) and a function is not a type, so the typed spelling of the
  same token is `Annotated[T, Inject(fn)]` (probe `E-55`). The row names `P-13` so the
  cross-register link is bidirectional.
- Row 6's `Why` cell now names `P-6`, which is what that row has always described.
- §6.4's closing sentence is PRD 04 §6.4's sentence, applied here: the parity list now names
  the silent skips that *are* parity (R-27 unresolvable string, R-28 primitive / non-`list`
  generic, R-25 class-body default) and then names the exception — R-23, `E-409`, row 9,
  registered as PRD 08 §5.9's P-13.
- §6.6 says "the divergences listed in §6.4" — no typed count to go stale.

**`prds/08-framework-compatibility-and-migration.md`** — P-6's and P-13's rows now cite the
§6.4 row that records them (`recorded as PRD 02 §6.4 row 6` / `row 9`), so the register is
navigable in both directions instead of one.

**`prds/check_consistency.py`** — new check 7, `registers`, with four rules:

| Rule | What it rejects |
|------|-----------------|
| contiguity | a register whose rows are not `1..n` — a deleted row shows as a hole, not a smaller register |
| the parity claim | a §6.4 closing sentence claiming "same silent cases" while any row's `lauren-di` column records silence (`silent`, `skipped`, `unset`, `leaves`, …) |
| counts | a sentence naming §6.4 with a typed count that is not the register's size — or, for "the four divergences (§6.4 rows 1–4)", not the size of the range it names |
| the cross-register link | a `P-n` in a §6.4 row that §5.9 does not define; a `PRD NN §6.4 row N` citation that does not resolve to a row naming that `P-n` back; a §5.9 row marked `**divergence:**` that the register sentence omits; a §5.9 count that disagrees with the entries the same sentence enumerates |

It also fails when one of the three expected registers disappears, so a renamed heading is a
red gate rather than a check that reads nothing, and it reports the census it read
(`registers: 3 §6.4 register(s) — 02 9 row(s), 03 11 row(s), 04 10 row(s); PRD 08 §5.9 holds
13 invariant(s)`). `prds/README.md` §6 lists it as check 7 with the same wording.

**`tests/unit/test_divergence_register_contract.py`** (16 tests) drives `check_registers`
over synthetic PRD sets, so each rule is pinned by the mutation it rejects rather than by
the corpus happening to trip it — the lesson goal #49 applied to the registry parser. One
test runs the rule against the live corpus.

## Measured before written / drills

- `grep -n "same silent cases" prds/02-*.md` → no matches (exit 1); the phrase survives only
  in PRD 03 §6.4, where no row records a silent-case divergence, and the check permits it
  there (proven by a test).
- §6.6 reverted to "the three divergences in §6.4" → **1 failure**:
  `02-…md:812: claims 'three divergences' but §6.4 (9 row(s))`.
- §6.4's closing sentence reverted to the unqualified form → **1 failure**:
  `02-…md:783: the closing sentence claims 'same silent cases' while row(s) [9] record a
  silent-case divergence — name the exception, as PRD 04 §6.4 does`.
- `P-13` removed from PRD 02's row 9 → **1 failure**:
  `08-…md P-13: cites PRD 02 §6.4 row 9, but that row does not name it back`.
- Each drill restored byte-identically (`md5sum -c`: OK).

Two defects in the check itself were found by these drills and fixed rather than papered
over: `REGISTER_COUNT` matched the `4` inside `§6.4 rows` (a section number is not a count —
now a `(?<![\d.])` lookbehind), and `_sentences` joined adjacent bullets into one sentence,
so a claim in one bullet could be measured against a section named in the next (now a
bullet/table/numbered-item boundary). A third — "the four divergences (§6.4 rows 1–4)" is a
claim about a subset, not the register — became the row-range rule rather than an exemption.

## Gates

| Command | Result |
|---------|--------|
| `python -m pytest -q` | **678 passed** |
| `python -m pytest tests/parity -q` | 56 passed |
| `python -m pytest tests/unit/test_divergence_register_contract.py -q` | 16 passed |
| `python prds/check_consistency.py` | all checks passed, including `registers: 3 §6.4 register(s) — 02 9, 03 11, 04 10` |
| `nox -s lint format docs_contract prds` | all four sessions successful |
| `nox -s ci` | all sessions passed |

`AGENTS.md`'s machine-checked quick-start figure moved `606 / 580` → **`622 / 596`**
(+16 tests; 622 + 56 parity = 678).
