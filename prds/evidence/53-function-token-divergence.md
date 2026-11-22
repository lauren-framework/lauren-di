# Goal #53 — the fourth divergence, registered

## The question

Goal #43 established that `lauren_di` **skips** a class-body field annotated with a *function*
token (`clock: Depends[make_clock]` → `field_deps == ()`) and that the reference **injects** it. It
recorded that in one place (`43-function-token-field.md`) and left the registers claiming three
divergences. Three documents therefore over-claimed, and the reference-side claim itself had no
probe behind it: `E-63` (`prds/evidence/prd04/f.py`) annotates `svc: Depends[Svc]` — a *class*.

## Shape (a): a parity invariant, not a documented non-invariant

Chosen because the harness already carries the machinery — `spec.RECORDED` holds a per-side outcome
per divergent row and `test_matrix.py` compares against it exactly — so a fourth divergence costs one
row, one `RECORDED` entry, one invariant and one adapter method, and in exchange *both* sides are
re-measured on every run. Shape (b) would have recorded the same fact as prose that nothing executes,
which is the defect this goal exists to close.

## The measurement

`prds/evidence/prd08/p2.py`, against the pinned checkout, re-recorded with
`PRDS_EVIDENCE_REFERENCE=/tmp/ref-clean /usr/bin/python3.14 prds/evidence/run_all.py`:

```
E-409  UsesDependsFunction field_deps= [('clock', 'make_clock')]
E-409b UsesDependsFunction clock -> Clock
```

`E-63`, the probe PRD 02 §6.2's R-23 was resting on, only ever exercised a class token; `E-409` is
the function half. The library's side is the existing measurement from goal #43 (`field_deps == ()`,
no attribute set).

## The four entries

| Entry | What the reference does | What `lauren_di` does | Decision |
|---|---|---|---|
| `values=` | `framework_values=` + process-wide `register_framework_value` | `value_tokens=` per container + `values=` | D-52 |
| P-6 | raises `MissingProviderError('str')` on `Annotated[str, Path]` (`E-404`) | leaves the field alone | D-25 |
| P-11 | no container-owned `startup()`/`shutdown()` (`E-420`, `E-423`) | owns both | D-53 |
| **P-13** | **collects a function-token field and injects the factory's value (`E-409`, `E-409b`)** | **leaves the field unset (PRD 02 §5.6 gates 4/6)** | **D-25, D-54** |

P-6 and P-13 are adjacent and deliberately not merged: both are class-body fields, but the reference
*raises* on one and *resolves* the other. A register naming only P-6 would describe the library's
silence as the mirror of a failure when for a function token it is the mirror of a success.

## The three places, made to agree

| Place | Before | After |
|---|---|---|
| PRD 08 §5.9 | "the **complete** divergence register: three entries" + 12 rows | 13 rows (P-13 added), "four entries", decisions `(D-25, D-52, D-53, D-54)`, plus a paragraph keeping P-6 and P-13 apart |
| PRD 04 §6.4 | 9 rows; "… same silent cases" | 10 rows (row 10 = the function token); the closing sentence names M-41 as the one silent case that is **not** parity and cites `E-409` |
| `tests/parity/backends.py` | `DIVERGENCES` of 3, docstring "Three differences … three entries" | `DIVERGENCES` of 4, docstring "Four differences … four entries", a `P-13` prose block, and a `depends(token)` adapter method |

Supporting citations added in the same change: PRD 08's three `P-1…P-12` mentions (→ `P-1…P-13`),
T08-31 (no longer restates a size), PRD 04 §5.9 rule 4 and M-41, and PRD 02 §6.2's R-23.

## The count is derived, not typed

The old guard was `assert len(DIVERGENCES) == 3` — a typed figure sitting inside the check built to
catch a wrong register, the failure class goal #51 fixed one file over. Now:

```python
_SIZE_RE = re.compile(r"divergence register:\s*(\w+)\s+entries?")
```

`spec.prd_register_size()` parses §5.9's own word and `test_harness` compares it with
`len(DIVERGENCES)`. The prose cannot claim a size the adapter does not hold, and the adapter cannot
grow an entry the prose does not name.

## Drills (each produced exactly one intended failure, then was restored)

| Planted | Result |
|---|---|
| `DIVERGENCES["P-13"]` deleted, PRD 08 left at four | `test_the_register_has_exactly_the_entries_the_prd_names` — named `{P-6, P-11, P-13, values}`, held `{P-6, P-11, values}` |
| PRD 08's sentence changed to "five entries" | same test — "§5.9 says the register is complete at 5 entries, found 4" |
| `RECORDED["P-13"]["lauren"]` changed to `"ignored"` | `test_p13_a_function_token_in_a_class_body_field[lauren]` — the reference did populate the field |
| `RECORDED["P-13"]["lauren_di"]` changed to `"injects"` | `test_p13_a_function_token_in_a_class_body_field[lauren_di]` — the library left it unset |
| P-13 row deleted from PRD 08 §5.9's table | `test_the_matrix_is_exactly_the_prd_table` — the PRD lists 12 rows, the suite implements 13 |

## Gates

```
python -m pytest tests/parity -q          ->  56 passed   (was 52: +2 backends, +1 MATRIX row, +1 RECORDED row)
python -m pytest -q                       ->  full suite green
python prds/check_consistency.py          ->  exit 0, all eight checks ok
PRDS_EVIDENCE_REFERENCE=/tmp/ref-clean /usr/bin/python3.14 prds/evidence/run_all.py --check
                                          ->  RESULT: PASS  (190 cited / 262 emitted / 72 allowed)
```

`AGENTS.md`'s quick-start parity figure moved `+52` → `+56`; the base figure is unchanged at
`595 (569 on 3.10)` because nothing outside `tests/parity/` was added.

### Recording note

The corpus records against a **clean** checkout, and `run_all.py` refuses a dirty one. The sibling
`../lauren-framework` carries uncommitted edits here, so the recording used the clean copy of the
same pin:

```
PRDS_EVIDENCE_REFERENCE=/tmp/ref-clean /usr/bin/python3.14 prds/evidence/run_all.py
```

`/tmp/ref-clean` is `0009d43` with a clean tree, the same commit as the sibling. Only three files
changed (`output/prd08/p2.out`, `output/ids.json`, `prd08/p2.py`) — no other transcript drifted,
which is what shows the re-record reproduced the corpus rather than overwriting it. The default
`python` on this machine is 3.13 and produces two *pre-existing* artefacts (`E-53`'s
`Annotated[fn_other, …]` repr and `E-306c`'s `ValueError` vs `TypeError`); recording under 3.14, the
interpreter `noxfile.py` names as `PRIMARY_PYTHON`, is what the corpus was captured with.
