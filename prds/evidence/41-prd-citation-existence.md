# Goal #41 — every `PRD NN` citation must name a document that exists

## The defect

PRD 01 §2's G8 row pointed the reader at a document that cannot exist:

```
$ git show 0e86d5d:prds/01-product-definition-and-public-api.md | sed -n '72p'
| G8 | Give every public name one canonical example | Every symbol in §5.3 has an entry in §5.4 and a tested example in PRD 10's docs contract |
```

The corpus is PRDs 01–08 plus `README.md`; there is no PRD 09 and the owner
forbids one, so no PRD 10 exists or will. The document the cell means is PRD 01
§10 row 3 — the `Examples` row, implemented by `tests/docs/test_prd_examples.py`.

Nothing caught it, and the reason is the interesting half. `cross-refs` matched

```python
re.finditer(r"PRD\s+0?(\d)\s*(?:§|section\s*)(\d+(?:\.\d+)*)", ...)
```

— a **single** digit, and a mandatory `§`. `PRD 10's docs contract` matches
neither: `0?(\d)` consumes the `1`, then `\s*(?:§|…)` cannot match the `0`, so
the pattern fails at every start position and the reference is not *rejected* —
it is **never seen**. The check reported "129 'PRD 0N §x' references checked"
and was telling the truth: it checked 129 references and this was not one of
them. Same shape as goal #38's two-goals-one-number and goal #39's
`scope.teardown_errors`: a well-formed-looking artefact that no mechanism reads.

## What changed

| File | Change |
| --- | --- |
| `prds/01-product-definition-and-public-api.md` | G8's acceptance cell now reads "…has an entry in §5.4 — one `####` subsection per symbol or tight pair, plus one grouped block shared by the thirteen `DIError` leaves — and every §5.4 block is compiled, with the `run` ones executed, by the PRD 01 §10 row 3 test (`tests/docs/test_prd_examples.py`)". It names the location, names the implementing module, and reuses §10.1's wording for the shape verbatim |
| `prds/check_consistency.py` | `cross-refs` now has two patterns: `_PRD_MENTION` (`\bPRDs?\s+(\d+)`, any number of digits, section or not) owns document existence, and `_PRD_SECTION` owns the section. A mention whose number names no file on disk fails; a section the target document does not have fails; a missing document is reported once per file, not twice. The corpus glob widens from `0*.md` to `[0-9][0-9]-*.md` — under `0*` a `10-*.md` was invisible to *every* check in the file while still being listable in README §2, which would have made "PRD 10 must exist" unsatisfiable rather than merely unenforced |
| `tests/unit/test_prd_cross_refs_contract.py` | seven tests over a synthetic corpus: a good citation passes and is counted; a two-digit document that **exists** passes (the control — otherwise "reject PRD 10" is satisfied by a blanket ban on two-digit numbers, a different and wrong rule); `PRD 10 §3` fails and is *counted*, which is what proves it was seen; the section-less `PRD 10's docs contract` fails; a stale section fails; a missing document is reported once; and the live corpus is clean with every citation counted |
| `tests/docs/test_prd_examples.py` | two tests pinning G8 itself: it must name the location §10 gives and the module §10 row 3 names, every `PRD NN` it cites must be a file in `prds/`, and it must use §10.1's words for the rule — derived from both rows, so neither can be paraphrased alone |
| `prds/README.md`, `.github/workflows/lint.yml` | the prose that describes the check now states the widened rule: every `PRD NN` names a document, every `PRD NN §x` names a heading of it |

The note line reports both counts, because they answer different questions:

```
cross-refs: 130 'PRD NN §x' reference(s) and 419 'PRD NN' mention(s) checked,
each against the document it names
```

## Drills — each mutation must fail, and does

```
--- DRILL A: 'PRD 10 §3' planted in PRD 01 (two digits, no such document)
exit=1
  FAIL [cross-ref] 01-product-definition-and-public-api.md: 'PRD 10' names no PRD on disk
       (present: 01, 02, 03, 04, 05, 06, 07, 08)

--- DRILL B: 'PRD 10's docs contract' planted (no section — the exact G8 shape)
exit=1
  FAIL [cross-ref] 01-product-definition-and-public-api.md: 'PRD 10' names no PRD on disk
       (present: 01, 02, 03, 04, 05, 06, 07, 08)

--- DRILL C: 'PRD 02 §9.9' planted (document exists, section does not)
exit=1
  FAIL [cross-ref] 01-product-definition-and-public-api.md: PRD 02 §9.9 does not exist

--- DRILL D: restored file
exit=0
  ok   cross-refs: 130 'PRD NN §x' reference(s) and 419 'PRD NN' mention(s) …
```

Drill B is the one that matters: it is G8's original sentence. Under the old
pattern it exited 0; under the new one it names the missing document.

```
--- DRILL E: G8 reverted to "a tested example in PRD 10's docs contract"
pytest exit=1
  FAILED test_the_goal_row_points_at_the_test_that_implements_it
  FAILED test_the_goal_row_uses_the_acceptance_row_s_words

--- DRILL F: G8 keeps the location but paraphrases the rule ("a subsection for each symbol")
pytest exit=1
  FAILED test_the_goal_row_uses_the_acceptance_row_s_words

--- DRILL G: §10.1's rule reworded, G8 left alone
pytest exit=1
  FAILED test_the_acceptance_row_states_the_rule_the_section_follows
  FAILED test_the_goal_row_uses_the_acceptance_row_s_words

--- DRILL H: restored
pytest exit=0   (35 passed in tests/docs/test_prd_examples.py)
```

F and G are the two directions of the same pin: the rule is stated twice, so
either copy drifting from the other is a failure.

## Verified on the committed tree

```
$ python prds/check_consistency.py
  ok   cross-refs: 130 'PRD NN §x' reference(s) and 419 'PRD NN' mention(s) checked,
       each against the document it names
  …
all checks passed

$ python -m pytest -q
596 passed

$ ruff check lauren_di tests scripts examples prds noxfile.py
All checks passed!

$ nox -s docs_contract llms_check
OK: 15 documentation file(s) satisfy the contract (50 relative link(s) resolved)
OK: all 38 public symbols of `lauren_di` are documented in llms-full.txt

$ nox -s ci
ci: all sessions passed
```

The section-reference count moves **129 → 130**: the corrected G8 cell adds one
`PRD 01 §10` citation. (The goal text quotes 121 → 122, measured before goals #39
and #41's own neighbours landed; the figure that matters is that it went *up* by
exactly one, because a reference that is skipped cannot be counted.) The
document-mention count is new — 419 — and it is the number that would have been
0 for `PRD 10` before this change, since a skipped reference is not counted at
all.

`AGENTS.md`'s quick-start count moves 535 → 544 (509 → 518 on 3.10): the seven
new cross-reference tests and the two new G8 tests. `GOALS.md`'s `'#NN'` count
moves 71 → 73 — §3 gains the rows that register #40 (the deleted cassette goal,
recorded so the range stays truthful) and #41.

## What this goal does *not* claim

It does not expand ranges. `PRD 02–07` checks that 02 exists and says nothing
about 07; the corpus contains no dangling range, and a range check would need a
stated rule for what `02–07` means when a document is missing from the middle.
It also does not check `PRD §5.4` (a section with no document number): the
corpus has none, and guessing which document such a reference means is exactly
the kind of inference that produced the defect this goal fixes.

## Where the literal still appears

`grep -rn "PRD 10" prds/` returns hits **only in this file** — the quotations of
the defect above (the old G8 cell and the drill transcripts). The corpus itself
is clean:

```
$ grep -rn "PRD 10" prds/0*.md prds/README.md prds/check_consistency.py
(no output)
```

That is deliberate rather than a loophole. This file exists to record the defect,
so it has to be able to name it — the same reason
`prds/evidence/38-goals-numbering.md` quotes the duplicate `### #36` heading its
own rule now rejects. `prds/evidence/` is outside the checker's corpus
(`prds/[0-9][0-9]-*.md`) and outside the docs contract's file set, so a quotation
here is not a citation any gate resolves.
